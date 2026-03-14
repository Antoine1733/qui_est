from __future__ import annotations

import base64
import difflib
import json
import os
import random
import unicodedata
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, session


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(ENV_FILE)


CHARACTERS_FILE = BASE_DIR / "data" / "characters.json"
PHOTO_DIR = BASE_DIR / "photo"
GROQ_ENDPOINT = os.getenv("GROQ_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
PHOTO_ANALYSIS_NOTICE = "D'accord alors attend je vais analyser la photo plus en detail."
SMALLTALK_REPLY = (
    "Bonjour ! Je ne donne pas d'indice automatiquement. "
    "Pose une question sur un attribut, ou ecris 'un indice' pour en recevoir un seul."
)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")

with CHARACTERS_FILE.open("r", encoding="utf-8") as f:
    CHARACTERS: list[dict[str, Any]] = json.load(f)

CHARACTER_BY_ID = {character["id"]: character for character in CHARACTERS}


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


# Global vocabulary: maps every normalized value-word (≥3 chars) to the attribute key
# it belongs to, built across ALL characters.
# Example: "lisse" → "type cheveux"  (because Loli has "type cheveux": "souvent lisse…")
# This lets us answer "lisse ?" for Victoria even though "lisse" is not in her own values.
_GLOBAL_VALUE_VOCAB: dict[str, str] = {}
for _char in CHARACTERS:
    for _k, _v in _char.get("attributes", {}).items():
        for _word in normalize_text(str(_v)).split():
            if len(_word) >= 3 and _word not in _GLOBAL_VALUE_VOCAB:
                _GLOBAL_VALUE_VOCAB[_word] = _k

def is_hint_request(question: str) -> bool:
    q = normalize_text(question)
    return any(token in q for token in ("indice", "hint", "aide"))


def is_smalltalk(question: str) -> bool:
    q = normalize_text(question).strip().strip("!?.,;:")
    if not q:
        return False

    tokens = q.split()
    greetings = {"bonjour", "salut", "hello", "bonsoir", "coucou", "yo", "bjr"}
    return bool(tokens) and tokens[0] in greetings and len(tokens) <= 4


def is_identity_request(question: str) -> bool:
    """Returns True if the player is asking who the secret character is."""
    q = normalize_text(question).strip().strip("!?.,;:")
    identity_phrases = (
        "c est qui", "cest qui", "qui est ce", "qui est-ce", "qui c est",
        "dis moi qui", "revele", "donne moi le nom", "quel est son nom",
        "c'est qui", "qui est la personne",
    )
    return any(phrase in q for phrase in identity_phrases)


def is_character_name_guess(question: str) -> bool:
    """Returns True if the question is just a character name (a direct guess typed in the chat)."""
    q = normalize_text(question).strip().strip("!?.,;: ")
    all_names = {normalize_text(c["name"]) for c in CHARACTERS}
    all_ids = {normalize_text(c["id"]) for c in CHARACTERS}
    return q in all_names or q in all_ids


def sanitize_single_hint(answer: str) -> str:
    compact = " ".join(answer.split())
    if not compact:
        return "Indice: information non disponible dans les attributs."

    for separator in ("|", ";", "\n"):
        if separator in compact:
            compact = compact.split(separator, 1)[0].strip()

    first_punctuation = [pos for pos in (compact.find("."), compact.find("!"), compact.find("?")) if pos >= 0]
    if first_punctuation:
        compact = compact[: min(first_punctuation) + 1].strip()

    if not compact.endswith((".", "!", "?")):
        compact += "."

    if not normalize_text(compact).startswith("indice"):
        compact = f"Indice: {compact}"

    return compact


def normalize_stem(text: str) -> str:
    return "".join(ch for ch in normalize_text(text) if ch.isalnum())


def extract_first_json_object(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if not text:
        return None

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def call_llm_completion(*, messages: list[dict[str, Any]], model: str, timeout: int) -> tuple[str | None, str]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None, "missing-api-key"

    payload = {
        "model": model,
        "temperature": 0,
        "messages": messages,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(GROQ_ENDPOINT, headers=headers, json=payload, timeout=timeout)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return None, f"http-{status}"
    except requests.RequestException:
        return None, "network-error"

    try:
        body = response.json()
        answer = body["choices"][0]["message"]["content"].strip()
        if not answer:
            raise ValueError("LLM returned an empty answer")
        return answer, "llm"
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None, "invalid-llm-response"


def build_llm_error_message(provider: str, *, for_vision: bool) -> str:
    if provider == "missing-api-key":
        if for_vision:
            return "Je n'arrive pas a analyser la photo car GROQ_API_KEY est absent. Ajoute-le dans .env puis redemarre l'app."
        return "Je n'arrive pas a recuperer les infos du LLM car GROQ_API_KEY est absent. Ajoute-le dans .env puis redemarre l'app."

    if provider == "http-401":
        if for_vision:
            return "La cle API semble invalide (401) pendant l'analyse photo. Verifie GROQ_API_KEY."
        return "La cle API semble invalide (401). Verifie GROQ_API_KEY."

    if provider == "http-429":
        if for_vision:
            return "Le service vision est temporairement limite (429). Reessaie dans quelques instants."
        return "Le service LLM est temporairement limite (429). Reessaie dans quelques instants."

    if provider.startswith("http-"):
        if for_vision:
            return f"L'analyse photo a echoue avec l'erreur {provider}."
        return f"L'appel au LLM a echoue avec l'erreur {provider}."

    if provider == "network-error":
        if for_vision:
            return "Je n'arrive pas a joindre le service d'analyse photo (erreur reseau)."
        return "Je n'arrive pas a joindre le service LLM (erreur reseau)."

    if provider == "invalid-llm-response":
        if for_vision:
            return "Le service vision a repondu avec un format inattendu."
        return "Le service LLM a repondu avec un format inattendu."

    if for_vision:
        return "Je n'arrive pas a determiner la reponse depuis la photo pour l'instant."
    return "Je n'arrive pas a recuperer les infos du LLM pour traiter ta question."


def resolve_photo_path(character: dict[str, Any]) -> Path | None:
    if not PHOTO_DIR.exists():
        return None

    explicit_photo = str(character.get("photo", "")).strip()
    if explicit_photo:
        explicit_path = PHOTO_DIR / explicit_photo
        if explicit_path.exists() and explicit_path.suffix.lower() == ".png":
            return explicit_path

    candidate_paths = [
        PHOTO_DIR / f"{character['name']}.png",
        PHOTO_DIR / f"{character['id']}.png",
    ]
    for candidate_path in candidate_paths:
        if candidate_path.exists():
            return candidate_path

    png_files = [path for path in PHOTO_DIR.iterdir() if path.is_file() and path.suffix.lower() == ".png"]
    if not png_files:
        return None

    photo_by_stem = {normalize_stem(path.stem): path for path in png_files}
    for source in (character.get("name", ""), character.get("id", ""), explicit_photo):
        normalized_source = normalize_stem(str(source))
        if not normalized_source:
            continue
        if normalized_source in photo_by_stem:
            return photo_by_stem[normalized_source]
        close_matches = difflib.get_close_matches(normalized_source, photo_by_stem.keys(), n=1, cutoff=0.82)
        if close_matches:
            return photo_by_stem[close_matches[0]]

    return None


def get_character_photo_url(character: dict[str, Any]) -> str:
    photo_path = resolve_photo_path(character)
    if photo_path is not None:
        return f"/photo/{photo_path.name}"
    return f"/avatar/{character['id']}.svg"


def serialize_character(character: dict[str, Any]) -> dict[str, str]:
    return {
        "id": character["id"],
        "name": character["name"],
        "photoUrl": get_character_photo_url(character),
    }


def get_secret_character() -> dict[str, Any] | None:
    secret_id = session.get("secret_character_id")
    if not secret_id:
        return None
    return CHARACTER_BY_ID.get(secret_id)


def start_new_game() -> None:
    secret_character = random.choice(CHARACTERS)
    session["secret_character_id"] = secret_character["id"]
    session["game_active"] = True
    session["question_count"] = 0


def find_matching_attribute(question: str, attributes: dict[str, Any]) -> tuple[str, Any] | None:
    """Returns (key, value) of the attribute that best matches the question intent.

    Strategy: value-matches are tried FIRST (more specific / intent-aligned),
    then key-only matches, then the global vocab fallback.  This avoids
    'cheveux bouclé ?' matching the generic key 'cheveux' (→ colour) instead of
    the value 'bouclés' in 'type cheveux'.
    """
    q_tokens = [t for t in normalize_text(question).split() if len(t) >= 3]
    if not q_tokens:
        return None

    # Separate indices: value words and key words
    value_word_to_attr: dict[str, tuple[str, Any]] = {}
    key_word_to_attr:   dict[str, tuple[str, Any]] = {}

    for k, v in attributes.items():
        nk = normalize_text(k)
        # index key words
        for word in nk.split():
            if word not in key_word_to_attr:
                key_word_to_attr[word] = (k, v)
        if nk not in key_word_to_attr:
            key_word_to_attr[nk] = (k, v)
        # index value words
        for word in normalize_text(str(v)).split():
            if len(word) >= 3 and word not in value_word_to_attr:
                value_word_to_attr[word] = (k, v)

    # Pass 1 — value-word match (most specific)
    for token in q_tokens:
        if token in value_word_to_attr:
            return value_word_to_attr[token]
        close = difflib.get_close_matches(token, value_word_to_attr.keys(), n=1, cutoff=0.75)
        if close:
            return value_word_to_attr[close[0]]

    # Pass 2 — key-word match
    for token in q_tokens:
        if token in key_word_to_attr:
            return key_word_to_attr[token]
        close = difflib.get_close_matches(token, key_word_to_attr.keys(), n=1, cutoff=0.75)
        if close:
            return key_word_to_attr[close[0]]

    # Pass 3 — global vocab fallback: token seen as VALUE on another character
    #           → map to the same key on THIS character
    for token in q_tokens:
        candidate_key = _GLOBAL_VALUE_VOCAB.get(token)
        if candidate_key is None:
            gm = difflib.get_close_matches(token, _GLOBAL_VALUE_VOCAB.keys(), n=1, cutoff=0.75)
            if gm:
                candidate_key = _GLOBAL_VALUE_VOCAB[gm[0]]
        if candidate_key is not None:
            for k, v in attributes.items():
                if normalize_text(k) == normalize_text(candidate_key):
                    return (k, v)

    return None


def question_maps_to_attribute(question: str, attributes: dict[str, Any]) -> bool:
    return find_matching_attribute(question, attributes) is not None


def answer_directly_from_attr(question: str, key: str, value: Any) -> str:
    """Deterministically answer a yes/no question from a confirmed attribute — no LLM needed.

    Rules:
    - value "oui" / "non" → "Oui." / "Non."
    - a question token appears in the value words → nuanced value (if multi-word) or "Oui."
    - no token found in value → "Non."
    """
    v_str = str(value).strip()
    v_norm = normalize_text(v_str)

    if v_norm == "oui":
        return "Oui."
    if v_norm == "non":
        return "Non."

    q_tokens = [t for t in normalize_text(question).split() if len(t) >= 3]
    v_words = v_norm.split()

    for token in q_tokens:
        if token in v_words:
            return v_str if len(v_words) > 1 else "Oui."
        if difflib.get_close_matches(token, v_words, n=1, cutoff=0.80):
            return v_str if len(v_words) > 1 else "Oui."

    # Question token absent from value → the attribute value doesn't match → Non.
    return "Non."


def classify_and_answer_with_attributes(
    question: str,
    attributes: dict[str, Any],
    *,
    hint_requested: bool,
) -> tuple[str, str, str]:
    # Find a confirmed attribute match BEFORE calling the LLM.
    matched_attr = find_matching_attribute(question, attributes)

    # When we have a confirmed match and this is NOT a hint request,
    # answer deterministically — no LLM call, no hallucination possible.
    if matched_attr and not hint_requested:
        key, value = matched_attr
        answer = answer_directly_from_attr(question, key, value)
        return "answer_from_attributes", answer, "direct-match"

    system_prompt = (
        "Tu es l'arbitre d'un jeu Qui Est. "
        "REGLE ABSOLUE : tu utilises EXCLUSIVEMENT les attributs JSON fournis. "
        "Il est STRICTEMENT INTERDIT d'inventer, deviner ou deduire une information "
        "qui ne figure pas explicitement dans les attributs. "
        "Reponds STRICTEMENT en JSON avec ce schema: "
        '{"decision":"answer_from_attributes|need_photo|unknown|smalltalk|single_hint","answer":"..."}. '
        "Regles: "
        "1) decision=smalltalk si c'est une salutation ou message social (bonjour, salut, etc.). "
        "Dans ce cas, answer NE DOIT DONNER AUCUN indice. "
        "2) Si demande_indice_explicite=false, il est strictement interdit de donner un indice gratuit. "
        "3) decision=single_hint UNIQUEMENT si demande_indice_explicite=true. "
        "Dans ce cas, answer contient EXACTEMENT UN SEUL indice base sur UN seul attribut. "
        "4) decision=answer_from_attributes UNIQUEMENT si la cle d'attribut correspondante est "
        "EXPLICITEMENT PRESENTE dans le JSON. Si la cle est absente, ne pas utiliser cette decision. "
        "Interprete les formulations telegraphiques (ex: 'cheveux blond ?') comme des questions binaires. "
        "Tolere les fautes de frappe courantes (ex: 'cheveyx' pour 'cheveux'). "
        "5) decision=need_photo si la question porte sur quelque chose de VISUEL ou VESTIMENTAIRE "
        "absent des attributs. Sont considerees comme visuelles/vestimentaires : "
        "vetements (pull, chemise, couleur vetement, tenue, veste, haut, bas, robe...), "
        "couleur de peau, silhouette precise, posture, expression, detail physique fin non liste. "
        "En cas de doute entre need_photo et unknown, privilegier need_photo. "
        "6) decision=unknown UNIQUEMENT si la question ne porte pas du tout sur le physique ou les vetements "
        "et que l'attribut correspondant est absent."
        "7) Pour une question avec decision=answer_from_attributes: "
        "   - Si la valeur de l'attribut est clairement oui/non, commence answer par 'Oui.' ou 'Non.'. "
        "   - Si la valeur est nuancee (ex: 'parfois', 'souvent', 'oui pour lire', 'entre X et Y'), "
        "     reponds fidelement cette valeur sans forcer Oui/Non. "
        "8) Si decision=unknown, answer doit etre: 'Information non disponible dans les attributs.'"
    )

    user_payload: dict[str, Any] = {
        "question": question,
        "demande_indice_explicite": hint_requested,
        "attributs_personnage_secret": attributes,
    }

    raw_answer, provider = call_llm_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        model=GROQ_MODEL,
        timeout=30,
    )

    if raw_answer is None:
        return "unknown", build_llm_error_message(provider, for_vision=False), provider

    parsed = extract_first_json_object(raw_answer)
    if parsed is None:
        normalized_raw = normalize_text(raw_answer)
        if "smalltalk" in normalized_raw:
            return "smalltalk", SMALLTALK_REPLY, "llm-router"
        if "single_hint" in normalized_raw:
            return "single_hint", sanitize_single_hint(raw_answer), "llm-router"
        if "need_photo" in normalized_raw:
            return "need_photo", "Information non disponible dans les attributs.", "llm-router"
        if "information non disponible" in normalized_raw:
            return "unknown", "Information non disponible dans les attributs.", "llm-router"
        return "unknown", "Information non disponible dans les attributs.", "llm-router"

    decision = str(parsed.get("decision", "")).strip().lower()
    answer = str(parsed.get("answer", "")).strip()

    if decision not in {"answer_from_attributes", "need_photo", "unknown", "smalltalk", "single_hint"}:
        decision = "unknown"

    # Sanity check: if LLM claims answer_from_attributes but no question token
    # maps to any attribute, it's hallucinating → redirect to need_photo.
    if decision == "answer_from_attributes" and not matched_attr:
        decision = "need_photo"
        answer = ""

    if not answer:
        if decision == "smalltalk":
            answer = SMALLTALK_REPLY
        elif decision == "single_hint":
            answer = "Indice: information non disponible dans les attributs."
        else:
            answer = "Information non disponible dans les attributs."

    if decision == "smalltalk":
        answer = SMALLTALK_REPLY
    elif decision == "single_hint":
        answer = sanitize_single_hint(answer)

    return decision, answer, "llm-router"


def recover_unknown_with_attributes(question: str, attributes: dict[str, Any]) -> tuple[bool, str, str]:
    """Second LLM pass to recover short or typo-heavy questions without hardcoded rules."""
    system_prompt = (
        "Tu es un resolveur de question pour Qui Est. "
        "REGLE ABSOLUE : utilise EXCLUSIVEMENT les attributs JSON fournis. "
        "Il est STRICTEMENT INTERDIT d'inventer, deviner ou deduire une information "
        "qui ne figure pas explicitement dans les attributs. "
        "Meme si la question porte sur quelque chose de logique ou probable, "
        "si la cle n'est pas dans le JSON, can_answer doit etre false. "
        "Interprete les formulations telegraphiques et fautes de frappe (ex: 'cheveyx blond ?'). "
        "Reponds STRICTEMENT en JSON avec ce schema: "
        '{"can_answer":true|false,"answer":"..."}. '
        "can_answer=true UNIQUEMENT si la cle d'attribut correspondante est PRESENTE dans le JSON. "
        "Si can_answer=true: "
        "  - Si la valeur de l'attribut est clairement oui/non, commence answer par 'Oui.' ou 'Non.'. "
        "  - Si la valeur est nuancee (ex: 'parfois', 'souvent', 'oui pour lire', 'entre X et Y'), "
        "    reponds fidelement cette valeur sans forcer Oui/Non. "
        "Si can_answer=false, answer doit etre exactement: 'Information non disponible dans les attributs.'"
    )

    payload = {
        "question": question,
        "attributs_personnage_secret": attributes,
    }

    raw_answer, provider = call_llm_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        model=GROQ_MODEL,
        timeout=30,
    )

    if raw_answer is None:
        return False, build_llm_error_message(provider, for_vision=False), provider

    parsed = extract_first_json_object(raw_answer)
    if parsed is None:
        normalized = normalize_text(raw_answer)
        if normalized.startswith("oui") or normalized.startswith("non"):
            return True, raw_answer.strip(), "llm-recovery"
        if "information non disponible" in normalized:
            return False, "Information non disponible dans les attributs.", "llm-recovery"
        return False, "Information non disponible dans les attributs.", "llm-recovery"

    can_answer = bool(parsed.get("can_answer", False))
    answer = str(parsed.get("answer", "")).strip() or "Information non disponible dans les attributs."
    return can_answer, answer, "llm-recovery"


def call_photo_llm(question: str, photo_path: Path | None) -> tuple[str, str]:
    if photo_path is None:
        return "Je n'ai pas trouve de photo PNG associee a cette personne.", "vision-unavailable"

    try:
        encoded_image = base64.b64encode(photo_path.read_bytes()).decode("ascii")
    except OSError:
        return "Je n'arrive pas a lire la photo PNG associee.", "vision-unavailable"

    system_prompt = (
        "Tu es l'arbitre du jeu Qui Est. "
        "REGLE ABSOLUE : reponds UNIQUEMENT a la question posee, rien de plus. "
        "Il est STRICTEMENT INTERDIT de decrire, mentionner ou donner un indice "
        "sur quoi que ce soit d'autre que ce qui est explicitement demande. "
        "Si la question est binaire (oui/non), reponds UNIQUEMENT par 'Oui.' ou 'Non.' — "
        "pas d'explication, pas de detail supplementaire, pas de description. "
        "Si ce n'est pas visible ou trop ambigu, reponds exactement et uniquement: "
        "'Je ne peux pas le determiner sur la photo.' "
        "Toute information non demandee est interdite."
    )

    raw_answer, provider = call_llm_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Question: "
                            f"{question}\n"
                            "Analyse cette photo PNG et reponds uniquement d'apres ce qui est visible."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded_image}"},
                    },
                ],
            },
        ],
        model=GROQ_VISION_MODEL,
        timeout=45,
    )

    if raw_answer is None:
        return build_llm_error_message(provider, for_vision=True), "vision-unavailable"

    # Safety net: keep only the first sentence to prevent gratuitous descriptions.
    first_sentence = raw_answer.strip().split(".")[0]
    if first_sentence:
        raw_answer = first_sentence.strip() + "."
    return raw_answer, "vision"


IDENTITY_REQUEST_REPLY = (
    "Je ne peux pas te dire qui c'est ! C'est le but du jeu. "
    "Pose des questions sur les attributs pour le decouvrir."
)
NAME_GUESS_REPLY = (
    "Pour deviner le personnage, elimine des cartes sur le plateau jusqu'a n'en avoir plus qu'une — "
    "la verification se fera automatiquement. Je ne confirme pas les noms dans le chat."
)


def is_clothing_question(question: str) -> bool:
    """Returns True if the question is clearly about clothing/outfit — must go to the photo.
    Rules:
    - A clothing noun alone → photo (ex: "tshirt blanc ?", "pull ?")
    - "porte" / "portait" alone → photo (ex: "porte un chapeau ?")
    - A color word alone does NOT trigger photo (ex: "yeux bleu ?" → attributes)
    - A color word + clothing noun → photo (ex: "veste rouge ?")
    """
    q = normalize_text(question)
    q_tokens = set(q.split())

    clothing_nouns = {
        "tshirt", "chemise", "pull", "gilet", "veste", "manteau", "blouson",
        "sweat", "hoodie", "polo", "pantalon", "jean", "short",
        "jupe", "robe", "costume", "cravate", "noeud", "echarpe",
        "casquette", "bonnet", "tenue", "vetement", "habit",
    }
    porter_verbs = {"porte", "portait", "porter"}
    color_words = {
        "blanc", "blanche", "noire", "noir", "rouge", "verte", "vert",
        "jaune", "gris", "grise", "rose", "orange", "violet", "violette",
        "beige", "rayure", "rayures", "carreaux",
    }

    has_clothing = bool(q_tokens & clothing_nouns)
    has_porter = bool(q_tokens & porter_verbs)
    has_color = bool(q_tokens & color_words)

    return has_clothing or has_porter or (has_color and has_clothing)


def answer_question(question: str, character: dict[str, Any]) -> tuple[str, str, str | None]:
    attributes = character.get("attributes", {})

    if is_identity_request(question):
        return IDENTITY_REQUEST_REPLY, "conversation-guard", None

    if is_character_name_guess(question):
        return NAME_GUESS_REPLY, "conversation-guard", None

    if is_smalltalk(question) and not is_hint_request(question):
        return SMALLTALK_REPLY, "conversation-guard", None

    # Short-circuit: clothing/appearance questions always go to the photo,
    # no LLM routing needed — prevents hallucination on vestimentary attrs.
    if is_clothing_question(question) and not is_hint_request(question):
        photo_answer, photo_provider = call_photo_llm(question, resolve_photo_path(character))
        return photo_answer, photo_provider, PHOTO_ANALYSIS_NOTICE

    decision, attribute_answer, provider = classify_and_answer_with_attributes(
        question,
        attributes,
        hint_requested=is_hint_request(question),
    )

    if decision in {"smalltalk", "single_hint"}:
        return attribute_answer, provider, None

    if decision == "answer_from_attributes":
        return attribute_answer, provider, None

    if decision == "need_photo":
        photo_answer, photo_provider = call_photo_llm(question, resolve_photo_path(character))
        return photo_answer, photo_provider, PHOTO_ANALYSIS_NOTICE

    if decision == "unknown":
        recovered, recovered_answer, recovered_provider = recover_unknown_with_attributes(question, attributes)
        if recovered:
            return recovered_answer, recovered_provider, None
        # Attribut absent et non-récupérable → tenter la photo comme dernier recours
        photo_answer, photo_provider = call_photo_llm(question, resolve_photo_path(character))
        return photo_answer, photo_provider, PHOTO_ANALYSIS_NOTICE

    return attribute_answer, provider, None


def render_avatar_svg(character: dict[str, Any]) -> str:
    attributes: dict[str, str] = character.get("attributes", {})

    hair_colors = {
        "brun": "#5B3A29",
        "blond": "#D8B24C",
        "roux": "#B44929",
        "noir": "#1E1E1E",
    }
    eye_colors = {
        "marron": "#4D3426",
        "bleu": "#2E86C1",
        "vert": "#2E8B57",
        "noir": "#232323",
    }
    background_palette = ["#F7C59F", "#CDE7BE", "#B4D4FF", "#F9E79F", "#F8C4D8", "#C8E6FF"]

    palette_index = sum(ord(ch) for ch in character["id"]) % len(background_palette)
    bg_color = background_palette[palette_index]
    hair_color = hair_colors.get(attributes.get("cheveux", "brun"), "#5B3A29")
    eye_color = eye_colors.get(attributes.get("yeux", "marron"), "#4D3426")

    glasses_svg = ""
    if attributes.get("lunettes") == "oui":
        glasses_svg = (
            '<rect x="88" y="124" width="28" height="20" rx="4" fill="none" '
            'stroke="#222" stroke-width="3" />'
            '<rect x="144" y="124" width="28" height="20" rx="4" fill="none" '
            'stroke="#222" stroke-width="3" />'
            '<line x1="116" y1="134" x2="144" y2="134" stroke="#222" stroke-width="3" />'
        )

    beard_svg = ""
    if attributes.get("barbe") == "oui":
        beard_svg = '<ellipse cx="130" cy="188" rx="42" ry="26" fill="#4A2D1B" opacity="0.8" />'

    moustache_svg = ""
    if attributes.get("moustache") == "oui":
        moustache_svg = (
            '<path d="M112 167 Q122 156 130 167 Q138 156 148 167" '
            'stroke="#3A2316" stroke-width="5" fill="none" stroke-linecap="round" />'
        )

    hat_svg = ""
    if attributes.get("chapeau") == "oui":
        hat_svg = (
            '<rect x="76" y="58" width="108" height="22" rx="9" fill="#2C3E50" />'
            '<rect x="96" y="30" width="68" height="34" rx="8" fill="#34495E" />'
        )

    accessory = attributes.get("accessoire", "aucun")
    accessory_svg = ""
    if accessory == "boucles":
        accessory_svg = (
            '<circle cx="84" cy="162" r="6" fill="#F4D03F" />'
            '<circle cx="176" cy="162" r="6" fill="#F4D03F" />'
        )
    elif accessory == "foulard":
        accessory_svg = (
            '<path d="M88 219 Q130 196 172 219 L160 242 Q130 229 100 242 Z" '
            'fill="#D35400" opacity="0.9" />'
        )
    elif accessory == "casque":
        accessory_svg = (
            '<path d="M76 120 Q130 68 184 120" stroke="#111" stroke-width="8" fill="none" />'
            '<rect x="66" y="116" width="14" height="38" rx="6" fill="#111" />'
            '<rect x="180" y="116" width="14" height="38" rx="6" fill="#111" />'
        )
    elif accessory == "cravate":
        accessory_svg = (
            '<path d="M130 220 L144 248 L130 278 L116 248 Z" fill="#922B21" />'
        )

    initial = character["name"][0].upper()

    return f"""<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 260 300\">
  <defs>
    <linearGradient id=\"g1\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"100%\">
      <stop offset=\"0%\" stop-color=\"{bg_color}\"/>
      <stop offset=\"100%\" stop-color=\"#FDF2E9\"/>
    </linearGradient>
  </defs>
  <rect width=\"260\" height=\"300\" rx=\"20\" fill=\"url(#g1)\"/>
  <circle cx=\"130\" cy=\"144\" r=\"66\" fill=\"#FAD7A0\"/>
  <ellipse cx=\"130\" cy=\"88\" rx=\"58\" ry=\"34\" fill=\"{hair_color}\"/>
  {hat_svg}
  <circle cx=\"108\" cy=\"138\" r=\"6\" fill=\"{eye_color}\"/>
  <circle cx=\"152\" cy=\"138\" r=\"6\" fill=\"{eye_color}\"/>
  <path d=\"M118 178 Q130 184 142 178\" stroke=\"#6E2C00\" stroke-width=\"4\" fill=\"none\" stroke-linecap=\"round\"/>
  <path d=\"M124 150 Q130 157 136 150\" stroke=\"#BB8F66\" stroke-width=\"3\" fill=\"none\" stroke-linecap=\"round\"/>
  {glasses_svg}
  {moustache_svg}
  {beard_svg}
  {accessory_svg}
  <rect x=\"72\" y=\"236\" width=\"116\" height=\"52\" rx=\"14\" fill=\"#FFFFFF\" opacity=\"0.75\"/>
  <text x=\"130\" y=\"270\" text-anchor=\"middle\" font-size=\"32\" font-family=\"Verdana, sans-serif\" fill=\"#1F2937\">{initial}</text>
</svg>
"""


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/new-game")
def new_game() -> Response:
    start_new_game()
    shuffled = CHARACTERS[:]
    random.shuffle(shuffled)

    return jsonify(
        {
            "characters": [serialize_character(character) for character in shuffled],
            "message": "Nouvelle partie lancee",
        }
    )


@app.post("/api/ask")
def ask() -> Response:
    secret_character = get_secret_character()
    if secret_character is None:
        return jsonify({"error": "No active game"}), 400

    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", "")).strip()
    if not question:
        return jsonify({"error": "Question is required"}), 400

    answer, provider, notice = answer_question(question, secret_character)

    session["question_count"] = session.get("question_count", 0) + 1
    question_number = session["question_count"]

    return jsonify({"answer": answer, "provider": provider, "notice": notice, "question_number": question_number})


@app.post("/api/final-check")
def final_check() -> Response:
    secret_character = get_secret_character()
    if secret_character is None:
        return jsonify({"error": "No active game"}), 400

    payload = request.get_json(silent=True) or {}
    guess_id = str(payload.get("guess_id", "")).strip()
    if not guess_id:
        return jsonify({"error": "guess_id is required"}), 400

    success = guess_id == secret_character["id"]
    question_count = session.get("question_count", 0)
    session["game_active"] = False

    return jsonify(
        {
            "success": success,
            "question_count": question_count,
            "secret": {
                "id": secret_character["id"],
                "name": secret_character["name"],
                "photoUrl": get_character_photo_url(secret_character),
                "attributes": secret_character.get("attributes", {}),
            },
            "message": "Bravo, tu as trouve !" if success else "Perdu, ce n'etait pas la bonne personne.",
        }
    )


@app.get("/photo/<path:filename>")
def photo(filename: str) -> Response:
    return send_from_directory(PHOTO_DIR, filename)


@app.get("/avatar/<character_id>.svg")
def avatar(character_id: str) -> Response:
    character = CHARACTER_BY_ID.get(character_id)
    if character is None:
        return Response("Unknown character", status=404)
    return Response(render_avatar_svg(character), mimetype="image/svg+xml")


if __name__ == "__main__":
    app.run(debug=True)