from __future__ import annotations

import base64
import difflib
import json
import os
import random
import re
import secrets
import psycopg2
import psycopg2.extras
import unicodedata
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, session
from werkzeug.utils import secure_filename


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
DATABASE_URL = os.getenv("DATABASE_URL", "")
PHOTO_DIR = BASE_DIR / "photo"
ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB
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
    _JSON_CHARACTERS: list[dict[str, Any]] = json.load(f)


# ── PostgreSQL helpers ───────────────────────────────────────────────────────

class _PgConn:
    """Thin shim making psycopg2 usable like sqlite3 in this codebase."""

    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql: str, params=None):
        self._cur.execute(sql, params)
        return self._cur

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()


def get_db() -> _PgConn:
    conn = psycopg2.connect(DATABASE_URL)
    return _PgConn(conn)


def init_db() -> None:
    PHOTO_DIR.mkdir(exist_ok=True)
    # Build a lookup from JSON for migration fallback
    _json_map: dict[str, dict[str, Any]] = {c["id"]: c for c in _JSON_CHARACTERS}
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                photo_filename TEXT,
                attributes TEXT NOT NULL DEFAULT '{}',
                is_preset INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deployments (
                id TEXT PRIMARY KEY,
                user_email TEXT NOT NULL,
                character_ids TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS game_characters (
                id TEXT NOT NULL,
                deployment_id TEXT NOT NULL,
                name TEXT NOT NULL,
                photo_filename TEXT,
                attributes TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (id, deployment_id),
                FOREIGN KEY (deployment_id) REFERENCES deployments(id) ON DELETE CASCADE
            )
        """)

        existing_preset_ids = {
            row["id"] for row in conn.execute("SELECT id FROM characters WHERE is_preset=1")
        }
        json_ids = {char["id"] for char in _JSON_CHARACTERS}

        # Remove preset characters that are no longer in characters.json
        stale_ids = existing_preset_ids - json_ids
        for stale_id in stale_ids:
            conn.execute("DELETE FROM characters WHERE id=%s", (stale_id,))

        # Clean up any non-preset chars from the global table (they belong only in game_characters)
        conn.execute("DELETE FROM characters WHERE is_preset=0")

        # Always sync ALL presets from JSON — presets are never deleted from the template library
        for char in _JSON_CHARACTERS:
            if char["id"] not in existing_preset_ids:
                conn.execute(
                    "INSERT INTO characters (id, name, photo_filename, attributes, is_preset) VALUES (%s,%s,%s,%s,1)",
                    (
                        char["id"],
                        char["name"],
                        char.get("photo"),
                        json.dumps(char.get("attributes", {}), ensure_ascii=False),
                    ),
                )
            else:
                conn.execute(
                    "UPDATE characters SET name=%s, attributes=%s WHERE id=%s AND is_preset=1",
                    (
                        char["name"],
                        json.dumps(char.get("attributes", {}), ensure_ascii=False),
                        char["id"],
                    ),
                )

        deployments = conn.execute("SELECT id, character_ids FROM deployments").fetchall()
        for dep in deployments:
            dep_id = dep["id"]
            try:
                char_ids = json.loads(dep["character_ids"])
            except (json.JSONDecodeError, TypeError):
                char_ids = []
            for cid in char_ids:
                already = conn.execute(
                    "SELECT 1 FROM game_characters WHERE id=%s AND deployment_id=%s", (cid, dep_id)
                ).fetchone()
                if already:
                    continue
                char_row = conn.execute(
                    "SELECT name, photo_filename, attributes FROM characters WHERE id=%s", (cid,)
                ).fetchone()
                if char_row:
                    conn.execute(
                        "INSERT INTO game_characters (id, deployment_id, name, photo_filename, attributes) VALUES (%s,%s,%s,%s,%s)",
                        (cid, dep_id, char_row["name"], char_row["photo_filename"], char_row["attributes"]),
                    )
                elif cid in _json_map:
                    jc = _json_map[cid]
                    conn.execute(
                        "INSERT INTO game_characters (id, deployment_id, name, photo_filename, attributes) VALUES (%s,%s,%s,%s,%s)",
                        (cid, dep_id, jc["name"], jc.get("photo"),
                         json.dumps(jc.get("attributes", {}), ensure_ascii=False)),
                    )

        conn.commit()


def load_characters_from_db() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, photo_filename, attributes, is_preset FROM characters ORDER BY is_preset DESC, name"
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        char: dict[str, Any] = {
            "id": row["id"],
            "name": row["name"],
            "attributes": json.loads(row["attributes"]),
            "is_preset": bool(row["is_preset"]),
        }
        if row["photo_filename"]:
            char["photo"] = row["photo_filename"]
        else:
            # Preset photos live in photo/ but aren't recorded in DB — resolve by name/id
            for _ext in (".png", ".jpg", ".jpeg", ".webp"):
                for _stem in (row["name"], row["id"]):
                    _candidate = PHOTO_DIR / f"{_stem}{_ext}"
                    if _candidate.exists():
                        char["photo"] = _candidate.name
                        break
                if "photo" in char:
                    break
        result.append(char)
    return result


def reload_characters() -> None:
    global CHARACTERS, CHARACTER_BY_ID
    CHARACTERS = load_characters_from_db()
    CHARACTER_BY_ID = {c["id"]: c for c in CHARACTERS}


def load_game_characters(deployment_id: str) -> list[dict[str, Any]]:
    """Load characters specific to a deployment from the game_characters table."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT gc.id, gc.name, gc.photo_filename, gc.attributes,
                      CASE WHEN c.id IS NOT NULL THEN 1 ELSE 0 END as is_preset
               FROM game_characters gc
               LEFT JOIN characters c ON gc.id = c.id
               WHERE gc.deployment_id = %s ORDER BY gc.name""",
            (deployment_id,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        char: dict[str, Any] = {
            "id": row["id"],
            "name": row["name"],
            "attributes": json.loads(row["attributes"]),
            "is_preset": bool(row["is_preset"]),
        }
        if row["photo_filename"]:
            char["photo"] = row["photo_filename"]
        else:
            # Fallback: look for photo file by name/id stem
            for _ext in (".png", ".jpg", ".jpeg", ".webp"):
                for _stem in (row["name"], row["id"]):
                    _candidate = PHOTO_DIR / f"{_stem}{_ext}"
                    if _candidate.exists():
                        char["photo"] = _candidate.name
                        break
                if "photo" in char:
                    break
        result.append(char)
    return result


# Bootstrap
init_db()
CHARACTERS: list[dict[str, Any]] = []
CHARACTER_BY_ID: dict[str, dict[str, Any]] = {}
reload_characters()


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


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
    deployment_id = session.get("deployment_id")
    chars = load_game_characters(deployment_id) if deployment_id else CHARACTERS
    all_names = {normalize_text(c["name"]) for c in chars}
    all_ids = {normalize_text(c["id"]) for c in chars}
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

    _PHOTO_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

    explicit_photo = str(character.get("photo", "")).strip()
    if explicit_photo:
        explicit_path = PHOTO_DIR / explicit_photo
        if explicit_path.exists() and explicit_path.suffix.lower() in _PHOTO_EXTS:
            return explicit_path

    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        for stem in (character["name"], character["id"]):
            candidate_path = PHOTO_DIR / f"{stem}{ext}"
            if candidate_path.exists():
                return candidate_path

    photo_files = [path for path in PHOTO_DIR.iterdir() if path.is_file() and path.suffix.lower() in _PHOTO_EXTS]
    if not photo_files:
        return None

    photo_by_stem = {normalize_stem(path.stem): path for path in photo_files}
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
    deployment_id = session.get("deployment_id")
    if deployment_id:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, name, photo_filename, attributes FROM game_characters WHERE id=%s AND deployment_id=%s",
                (secret_id, deployment_id),
            ).fetchone()
        if row:
            char: dict[str, Any] = {
                "id": row["id"],
                "name": row["name"],
                "attributes": json.loads(row["attributes"]),
            }
            if row["photo_filename"]:
                char["photo"] = row["photo_filename"]
            return char
    return CHARACTER_BY_ID.get(secret_id)


def start_new_game() -> None:
    secret_character = random.choice(CHARACTERS)
    session["secret_character_id"] = secret_character["id"]
    session["game_active"] = True
    session["question_count"] = 0


def classify_and_answer_with_attributes(
    question: str,
    attributes: dict[str, Any],
    *,
    hint_requested: bool,
) -> tuple[str, str, str]:
    attribute_keys = list(attributes.keys())
    system_prompt = (
        "Tu es l'arbitre d'un jeu Qui Est-ce que c'est.\n"
        "Tu recois une question du joueur et les attributs JSON du personnage secret.\n"
        "Reponds STRICTEMENT en JSON avec ces trois champs :\n"
        '{"sujet": "le nom/sujet principal de la question, ou \"aucun\" si la question ne contient que des adjectifs/couleurs sans nom",'
        '"decision":"answer_from_attributes|need_photo|smalltalk|single_hint|clarify","answer":"..."}\n'
        "\n"
        "ETAPE 1 — Identifie le SUJET de la question et ecris-le dans le champ 'sujet'.\n"
        "  Un SUJET est un NOM (chose, partie du corps, vetement, lieu…).\n"
        "  sujet='aucun' si la question ne contient QUE des adjectifs ou couleurs sans nom.\n"
        "  Exemples :\n"
        "    'pull rouge' → sujet='pull'\n"
        "    'yeux bleus' → sujet='yeux'\n"
        "    'ciel bleu' → sujet='ciel'\n"
        "    'cheveux roux' → sujet='cheveux'\n"
        "    'rouge' → sujet='aucun'\n"
        "    'bleu' → sujet='aucun'\n"
        "    'orange' → sujet='aucun'\n"
        "    'grand' → sujet='aucun'\n"
        "\n"
        "ETAPE 2 — Choisis la decision selon cet arbre :\n"
        "  a) Salutation/social → smalltalk.\n"
        "  b) demande_indice_explicite=true → single_hint.\n"
        "  c) sujet='aucun' → clarify, answer=question courte ex: 'Rouge pour quoi ? Les cheveux, les yeux, les vetements, autre chose ?'\n"
        f"  d) sujet present dans les cles {attribute_keys} (ou variante/faute de frappe proche) → answer_from_attributes.\n"
        "     REGLE TYPE DE QUESTION :\n"
        "     - La question contient UN ADJECTIF/COULEUR en plus du sujet → TOUJOURS binaire → 'Oui.' ou 'Non.'\n"
        "       ex: 'cheveux rouge ?' avec cheveux=brun → 'Non.' (pas 'Brun.')\n"
        "       ex: 'yeux bleus ?' avec yeux=bleu → 'Oui.'\n"
        "       ex: 'grande taille ?' avec taille=grande → 'Oui.'\n"
        "     - La question ne contient que le sujet sans adjectif → ouverte → valeur de l'attribut.\n"
        "       ex: 'couleur cheveux ?' avec cheveux=brun → 'Brun.'\n"
        "       ex: 'homme ou femme ?' avec genre=homme → 'Homme.'\n"
        "     - Booleen : lunettes=oui → 'Oui.' ; lunettes=non → 'Non.'\n"
        "  d bis) sujet ABSENT des cles MAIS est un ADJECTIF/VALEUR qui qualifie implicitement un attribut\n"
        "     (couleur de cheveux, genre, taille, corpulence, etc.),\n"
        "     y compris si CE PERSONNAGE n'a pas cette valeur (ex: 'blond' mais cheveux=brun)\n"
        "     → answer_from_attributes. Utilise le NOM DE LA CLE correspondante comme 'sujet'.\n"
        "     La reponse est TOUJOURS binaire : 'Oui.' si la valeur reelle correspond, 'Non.' sinon.\n"
        "     ex: question='homme ?' et genre=homme → sujet='genre', answer='Oui.'\n"
        "     ex: question='homme ?' et genre=femme → sujet='genre', answer='Non.'\n"
        "     ex: question='blond ?' et cheveux=brun → sujet='cheveux', answer='Non.'\n"
        "     ex: question='blond ?' et cheveux=blond → sujet='cheveux', answer='Oui.'\n"
        "     ex: question='feme ?' (faute) et genre=femme → sujet='genre', answer='Oui.'\n"
        "     ex: question='grande ?' et taille=grand → sujet='taille', answer='Oui.'\n"
        "     ex: question='mince ?' et corpulence=mince → sujet='corpulence', answer='Oui.'\n"
        "  e) sujet identifie mais ABSENT des cles ET ABSENT des valeurs → need_photo.\n"
        "     JAMAIS repondre 'Non.' par deduction. Absence de cle ET de valeur = need_photo."
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
        return "need_photo", build_llm_error_message(provider, for_vision=False), provider

    parsed = extract_first_json_object(raw_answer)
    if parsed is None:
        return "need_photo", "", "llm-router"

    decision = str(parsed.get("decision", "")).strip().lower()
    answer = str(parsed.get("answer", "")).strip()
    sujet = str(parsed.get("sujet", "")).strip().lower()

    if decision not in {"answer_from_attributes", "need_photo", "smalltalk", "single_hint", "clarify"}:
        decision = "need_photo"

    # Safety: if the LLM identified a real subject but still returned clarify, override to need_photo.
    if decision == "clarify" and sujet and sujet != "aucun":
        decision = "need_photo"
        answer = ""

    # Safety: if the LLM returned answer_from_attributes but the identified subject is NOT
    # in the attribute keys, override to need_photo (e.g. sujet="pull" with no "pull" key).
    # Exception: if sujet is a VALUE that exists anywhere across all characters (e.g. sujet="blond"
    # even if THIS character has cheveux="brun"), the LLM is handling a value-as-question.
    if decision == "answer_from_attributes" and sujet and sujet != "aucun":
        normalized_sujet = normalize_text(sujet)
        normalized_keys = [normalize_text(k) for k in attributes.keys()]
        all_values_global = [normalize_text(str(v)) for c in CHARACTERS for v in c.get("attributes", {}).values()]
        sujet_in_keys = any(
            normalized_sujet in k or k in normalized_sujet or
            difflib.get_close_matches(normalized_sujet, normalized_keys, n=1, cutoff=0.75)
            for k in normalized_keys
        )
        sujet_is_known_value = bool(
            normalized_sujet in all_values_global or
            difflib.get_close_matches(normalized_sujet, list(set(all_values_global)), n=1, cutoff=0.75)
        )
        if not sujet_in_keys and not sujet_is_known_value:
            decision = "need_photo"
            answer = ""

    # Safety: if the LLM returned smalltalk but the question is NOT a real social greeting
    # (e.g. "le ciel est bleu ?"), redirect to need_photo so the photo is analysed.
    if decision == "smalltalk" and not is_smalltalk(question):
        decision = "need_photo"
        answer = ""

    if not answer:
        if decision == "smalltalk":
            answer = SMALLTALK_REPLY
        elif decision == "single_hint":
            answer = "Indice: information non disponible dans les attributs."

    if decision == "smalltalk":
        answer = answer or SMALLTALK_REPLY
    elif decision == "single_hint":
        answer = sanitize_single_hint(answer)
    elif decision == "clarify" and not answer:
        answer = "Peux-tu preciser ta question ? (ex: de quoi parles-tu exactement ?)"

    return decision, answer, "llm-router"




_MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def call_photo_llm(question: str, photo_path: Path | None) -> tuple[str, str]:
    if photo_path is None:
        return "Je n'ai pas trouve de photo associee a cette personne.", "vision-unavailable"

    try:
        encoded_image = base64.b64encode(photo_path.read_bytes()).decode("ascii")
    except OSError:
        return "Je n'arrive pas a lire la photo associee.", "vision-unavailable"

    mime_type = _MIME_BY_EXT.get(photo_path.suffix.lower(), "image/png")

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
                            "Analyse cette photo et reponds uniquement d'apres ce qui est visible."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded_image}"},
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


def answer_question(question: str, character: dict[str, Any]) -> tuple[str, str, str | None]:
    attributes = character.get("attributes", {})

    if is_identity_request(question):
        return IDENTITY_REQUEST_REPLY, "conversation-guard", None

    if is_character_name_guess(question):
        return NAME_GUESS_REPLY, "conversation-guard", None

    if is_smalltalk(question) and not is_hint_request(question):
        return SMALLTALK_REPLY, "conversation-guard", None

    q_norm = normalize_text(question)
    if any(token in q_norm for token in ("prenom", "s appelle", "appelle-t-il", "appelle-t-elle", "comment il s", "comment elle s")):
        return "On ne peut pas poser de questions sur le prénom.", "conversation-guard", None

    decision, answer, provider = classify_and_answer_with_attributes(
        question,
        attributes,
        hint_requested=is_hint_request(question),
    )

    if decision in {"smalltalk", "single_hint", "answer_from_attributes", "clarify"}:
        return answer, provider, None

    # need_photo: LLM determined info is absent or visual → analyze the photo
    photo_answer, photo_provider = call_photo_llm(question, resolve_photo_path(character))
    return photo_answer, photo_provider, PHOTO_ANALYSIS_NOTICE


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
    show_login = not session.get("user_email") or request.args.get("login") == "required"
    return render_template(
        "index.html",
        user_email=session.get("user_email", ""),
        show_login_modal=show_login,
        next_url=request.args.get("next", ""),
        deployment_id="",
        show_admin=True,
    )


@app.get("/api/new-game")
def new_game() -> Response:
    deployment_id = request.args.get("d", "").strip()
    if deployment_id:
        with get_db() as conn:
            row = conn.execute("SELECT id FROM deployments WHERE id=%s", (deployment_id,)).fetchone()
        if not row:
            return jsonify({"error": "Lien de jeu introuvable."}), 404
        chars = load_game_characters(deployment_id)
        if not chars:
            return jsonify({"error": "Aucun personnage dans ce déploiement."}), 404
        secret = random.choice(chars)
        session["secret_character_id"] = secret["id"]
        session["deployment_id"] = deployment_id
        session["game_active"] = True
        session["question_count"] = 0
        shuffled = chars[:]
        random.shuffle(shuffled)
        return jsonify({"characters": [serialize_character(c) for c in shuffled], "message": "Nouvelle partie lancée"})
    session.pop("deployment_id", None)
    reload_characters()
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
        # Custom char not in global presets — try game_characters using session deployment
        deployment_id = session.get("deployment_id")
        if deployment_id:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT id, name, attributes FROM game_characters WHERE id=%s AND deployment_id=%s",
                    (character_id, deployment_id),
                ).fetchone()
            if row:
                character = {"id": row["id"], "name": row["name"],
                             "attributes": json.loads(row["attributes"])}
        if character is None:
            # Generic fallback: render an avatar with just the id for color seeding
            character = {"id": character_id, "name": character_id, "attributes": {}}
    return Response(render_avatar_svg(character), mimetype="image/svg+xml")


# ── Admin routes ──────────────────────────────────────────────────────────────

@app.get("/admin")
def admin_page() -> Response | str:
    email = session.get("user_email")
    if not email:
        return redirect("/?login=required&next=/admin")
    return render_template("admin.html", characters=load_characters_from_db(), user_email=email)


@app.post("/api/admin/characters")
def admin_add_character() -> Response:
    name = str(request.form.get("name", "")).strip()
    if not name:
        return jsonify({"error": "Le nom est requis."}), 400

    # Generate a URL-safe unique ID from the name
    base_id = re.sub(r"[^a-z0-9]+", "-", normalize_text(name)).strip("-")
    if not base_id:
        base_id = "personnage"
    character_id = base_id
    with get_db() as conn:
        suffix = 1
        while conn.execute("SELECT 1 FROM characters WHERE id=%s", (character_id,)).fetchone():
            character_id = f"{base_id}-{suffix}"
            suffix += 1

    # Handle photo upload
    photo_filename = None
    photo_file = request.files.get("photo")
    if photo_file and photo_file.filename:
        ext = photo_file.filename.rsplit(".", 1)[-1].lower() if "." in photo_file.filename else ""
        if ext not in ALLOWED_PHOTO_EXTENSIONS:
            return jsonify({"error": f"Format non supporté. Acceptés : {', '.join(sorted(ALLOWED_PHOTO_EXTENSIONS))}"}), 400
        file_bytes = photo_file.read()
        if len(file_bytes) > MAX_PHOTO_BYTES:
            return jsonify({"error": "Photo trop grande (max 8 Mo)."}), 400
        safe_name = secure_filename(f"{character_id}.{ext}")
        PHOTO_DIR.mkdir(exist_ok=True)
        (PHOTO_DIR / safe_name).write_bytes(file_bytes)
        photo_filename = safe_name

    # Parse attributes JSON
    try:
        attributes_raw = str(request.form.get("attributes", "{}")).strip() or "{}"
        attributes = json.loads(attributes_raw)
        if not isinstance(attributes, dict):
            raise ValueError("attributes must be a JSON object")
    except (json.JSONDecodeError, ValueError):
        return jsonify({"error": "Format d'attributs invalide (JSON attendu)."}), 400

    with get_db() as conn:
        conn.execute(
            "INSERT INTO characters (id, name, photo_filename, attributes, is_preset) VALUES (%s,%s,%s,%s,0)",
            (character_id, name, photo_filename, json.dumps(attributes, ensure_ascii=False)),
        )
        conn.commit()

    reload_characters()
    return jsonify({"success": True, "id": character_id, "name": name,
                    "photo": photo_filename, "attributes": attributes, "is_preset": False}), 201


@app.delete("/api/admin/characters/<character_id>")
def admin_delete_character(character_id: str) -> Response:
    with get_db() as conn:
        row = conn.execute(
            "SELECT is_preset, photo_filename FROM characters WHERE id=%s", (character_id,)
        ).fetchone()
        if row is None:
            return jsonify({"error": "Personnage introuvable."}), 404
        if bool(row["is_preset"]):
            return jsonify({"error": "Les personnages préenregistrés ne peuvent pas être supprimés de la bibliothèque."}), 403
        # Cascade: remove from ALL games that have this character
        conn.execute("DELETE FROM game_characters WHERE id=%s", (character_id,))
        conn.execute("DELETE FROM characters WHERE id=%s", (character_id,))
        # Delete photo file (no more references after cascade)
        if row["photo_filename"]:
            photo_path = PHOTO_DIR / row["photo_filename"]
            if photo_path.exists():
                photo_path.unlink(missing_ok=True)
        conn.commit()

    reload_characters()
    return jsonify({"success": True})


@app.post("/api/admin/characters/<character_id>/edit")
def admin_edit_character(character_id: str) -> Response:
    with get_db() as conn:
        row = conn.execute(
            "SELECT photo_filename FROM characters WHERE id=%s", (character_id,)
        ).fetchone()
        if row is None:
            return jsonify({"error": "Personnage introuvable."}), 404

        # Handle photo upload
        photo_filename = row["photo_filename"]
        photo_file = request.files.get("photo")
        if photo_file and photo_file.filename:
            ext = photo_file.filename.rsplit(".", 1)[-1].lower() if "." in photo_file.filename else ""
            if ext not in ALLOWED_PHOTO_EXTENSIONS:
                return jsonify({"error": f"Format non supporté. Acceptés : {', '.join(sorted(ALLOWED_PHOTO_EXTENSIONS))}"}), 400
            file_bytes = photo_file.read()
            if len(file_bytes) > MAX_PHOTO_BYTES:
                return jsonify({"error": "Photo trop grande (max 8 Mo)."}), 400
            # Remove old photo if different
            if photo_filename and (PHOTO_DIR / photo_filename).exists():
                (PHOTO_DIR / photo_filename).unlink(missing_ok=True)
            safe_name = secure_filename(f"{character_id}.{ext}")
            PHOTO_DIR.mkdir(exist_ok=True)
            (PHOTO_DIR / safe_name).write_bytes(file_bytes)
            photo_filename = safe_name

        # Parse attributes
        try:
            attributes_raw = str(request.form.get("attributes", "{}")).strip() or "{}"
            attributes = json.loads(attributes_raw)
            if not isinstance(attributes, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            return jsonify({"error": "Format d'attributs invalide (JSON attendu)."}), 400

        conn.execute(
            "UPDATE characters SET photo_filename=%s, attributes=%s WHERE id=%s",
            (photo_filename, json.dumps(attributes, ensure_ascii=False), character_id),
        )
        conn.commit()

    reload_characters()
    return jsonify({"success": True, "id": character_id, "photo": photo_filename,
                    "attributes": attributes})


# ── Per-game character routes ─────────────────────────────────────────────────

def _check_game_ownership(deploy_id: str, email: str):
    """Return the deployment row if it belongs to email, else None."""
    with get_db() as conn:
        return conn.execute(
            "SELECT id FROM deployments WHERE id=%s AND user_email=%s", (deploy_id, email)
        ).fetchone()


@app.get("/api/admin/game/<deploy_id>/characters")
def admin_game_characters(deploy_id: str) -> Response:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Non connecté."}), 401
    if not _check_game_ownership(deploy_id, email):
        return jsonify({"error": "Introuvable ou accès refusé."}), 404
    chars = load_game_characters(deploy_id)
    result = []
    for char in chars:
        entry: dict[str, Any] = {
            "id": char["id"], "name": char["name"],
            "attributes": char.get("attributes", {}), "is_preset": char.get("is_preset", False),
        }
        if char.get("photo"):
            entry["photo"] = char["photo"]
        result.append(entry)
    return jsonify(result)


@app.post("/api/admin/game/<deploy_id>/characters")
def admin_game_add_character(deploy_id: str) -> Response:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Non connecté."}), 401
    if not _check_game_ownership(deploy_id, email):
        return jsonify({"error": "Introuvable ou accès refusé."}), 404

    name = str(request.form.get("name", "")).strip()
    if not name:
        return jsonify({"error": "Le nom est requis."}), 400

    base_id = re.sub(r"[^a-z0-9]+", "-", normalize_text(name)).strip("-") or "personnage"
    character_id = base_id
    with get_db() as conn:
        suffix = 1
        while conn.execute(
            "SELECT 1 FROM game_characters WHERE id=%s AND deployment_id=%s", (character_id, deploy_id)
        ).fetchone():
            character_id = f"{base_id}-{suffix}"
            suffix += 1

    photo_filename = None
    photo_file = request.files.get("photo")
    if photo_file and photo_file.filename:
        ext = photo_file.filename.rsplit(".", 1)[-1].lower() if "." in photo_file.filename else ""
        if ext not in ALLOWED_PHOTO_EXTENSIONS:
            return jsonify({"error": f"Format non supporté. Acceptés : {', '.join(sorted(ALLOWED_PHOTO_EXTENSIONS))}"}), 400
        file_bytes = photo_file.read()
        if len(file_bytes) > MAX_PHOTO_BYTES:
            return jsonify({"error": "Photo trop grande (max 8 Mo)."}), 400
        safe_name = secure_filename(f"{deploy_id}-{character_id}.{ext}")
        PHOTO_DIR.mkdir(exist_ok=True)
        (PHOTO_DIR / safe_name).write_bytes(file_bytes)
        photo_filename = safe_name

    try:
        attributes_raw = str(request.form.get("attributes", "{}")).strip() or "{}"
        attributes = json.loads(attributes_raw)
        if not isinstance(attributes, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return jsonify({"error": "Format d'attributs invalide (JSON attendu)."}), 400

    with get_db() as conn:
        conn.execute(
            "INSERT INTO game_characters (id, deployment_id, name, photo_filename, attributes) VALUES (%s,%s,%s,%s,%s)",
            (character_id, deploy_id, name, photo_filename, json.dumps(attributes, ensure_ascii=False)),
        )
        # Keep character_ids in deployments in sync
        dep = conn.execute("SELECT character_ids FROM deployments WHERE id=%s", (deploy_id,)).fetchone()
        if dep:
            cur_ids = json.loads(dep["character_ids"])
            if character_id not in cur_ids:
                cur_ids.append(character_id)
                conn.execute("UPDATE deployments SET character_ids=%s WHERE id=%s", (json.dumps(cur_ids), deploy_id))
        conn.commit()

    return jsonify({"success": True, "id": character_id, "name": name,
                    "photo": photo_filename, "attributes": attributes, "is_preset": False}), 201


@app.delete("/api/admin/game/<deploy_id>/characters/<character_id>")
def admin_game_delete_character(deploy_id: str, character_id: str) -> Response:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Non connecté."}), 401
    if not _check_game_ownership(deploy_id, email):
        return jsonify({"error": "Introuvable ou accès refusé."}), 404

    with get_db() as conn:
        row = conn.execute(
            "SELECT photo_filename FROM game_characters WHERE id=%s AND deployment_id=%s",
            (character_id, deploy_id),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Personnage introuvable."}), 404

        conn.execute(
            "DELETE FROM game_characters WHERE id=%s AND deployment_id=%s", (character_id, deploy_id)
        )
        # Keep character_ids in deployments in sync
        dep = conn.execute("SELECT character_ids FROM deployments WHERE id=%s", (deploy_id,)).fetchone()
        if dep:
            cur_ids = [cid for cid in json.loads(dep["character_ids"]) if cid != character_id]
            conn.execute("UPDATE deployments SET character_ids=%s WHERE id=%s", (json.dumps(cur_ids), deploy_id))
        conn.commit()

    return jsonify({"success": True})


@app.post("/api/admin/game/<deploy_id>/characters/<character_id>/edit")
def admin_game_edit_character(deploy_id: str, character_id: str) -> Response:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Non connecté."}), 401
    if not _check_game_ownership(deploy_id, email):
        return jsonify({"error": "Introuvable ou accès refusé."}), 404

    with get_db() as conn:
        row = conn.execute(
            "SELECT photo_filename FROM game_characters WHERE id=%s AND deployment_id=%s",
            (character_id, deploy_id),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Personnage introuvable."}), 404

        photo_filename = row["photo_filename"]
        photo_file = request.files.get("photo")
        if photo_file and photo_file.filename:
            ext = photo_file.filename.rsplit(".", 1)[-1].lower() if "." in photo_file.filename else ""
            if ext not in ALLOWED_PHOTO_EXTENSIONS:
                return jsonify({"error": f"Format non supporté. Acceptés : {', '.join(sorted(ALLOWED_PHOTO_EXTENSIONS))}"}), 400
            file_bytes = photo_file.read()
            if len(file_bytes) > MAX_PHOTO_BYTES:
                return jsonify({"error": "Photo trop grande (max 8 Mo)."}), 400
            if photo_filename and (PHOTO_DIR / photo_filename).exists():
                (PHOTO_DIR / photo_filename).unlink(missing_ok=True)
            safe_name = secure_filename(f"{deploy_id}-{character_id}.{ext}")
            PHOTO_DIR.mkdir(exist_ok=True)
            (PHOTO_DIR / safe_name).write_bytes(file_bytes)
            photo_filename = safe_name

        try:
            attributes_raw = str(request.form.get("attributes", "{}")).strip() or "{}"
            attributes = json.loads(attributes_raw)
            if not isinstance(attributes, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            return jsonify({"error": "Format d'attributs invalide (JSON attendu)."}), 400

        conn.execute(
            "UPDATE game_characters SET photo_filename=%s, attributes=%s WHERE id=%s AND deployment_id=%s",
            (photo_filename, json.dumps(attributes, ensure_ascii=False), character_id, deploy_id),
        )
        conn.commit()

    return jsonify({"success": True, "id": character_id, "photo": photo_filename, "attributes": attributes})


@app.post("/api/auth/login")
def auth_login() -> Response:
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Adresse e-mail invalide."}), 400
    session["user_email"] = email
    return jsonify({"email": email})


@app.post("/api/auth/logout")
def auth_logout() -> Response:
    session.pop("user_email", None)
    return jsonify({"success": True})


@app.get("/api/me")
def api_me() -> Response:
    return jsonify({"email": session.get("user_email")})


# ── Deploy routes ─────────────────────────────────────────────────────────────

@app.post("/api/deploy")
def api_deploy() -> Response:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Connexion requise."}), 401
    data = request.get_json(silent=True) or {}
    char_ids = data.get("character_ids", [])
    new_chars = data.get("new_chars", [])
    if not isinstance(char_ids, list):
        char_ids = []
    if not isinstance(new_chars, list):
        new_chars = []
    reload_characters()
    valid_ids = {c["id"] for c in CHARACTERS}
    char_ids = [cid for cid in char_ids if isinstance(cid, str) and cid in valid_ids]
    new_chars = [nc for nc in new_chars
                 if isinstance(nc, dict) and isinstance(nc.get("name"), str) and nc["name"].strip()]
    if not char_ids and not new_chars:
        return jsonify({"error": "Ajoute au moins un personnage."}), 400
    deploy_id = secrets.token_hex(4)
    with get_db() as conn:
        while conn.execute("SELECT 1 FROM deployments WHERE id=%s", (deploy_id,)).fetchone():
            deploy_id = secrets.token_hex(4)
        conn.execute(
            "INSERT INTO deployments (id, user_email, character_ids) VALUES (%s,%s,%s)",
            (deploy_id, email, json.dumps(char_ids)),
        )
        # Copy selected preset characters into game_characters
        for cid in char_ids:
            char = CHARACTER_BY_ID.get(cid)
            if char:
                conn.execute(
                    "INSERT INTO game_characters (id, deployment_id, name, photo_filename, attributes) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (cid, deploy_id, char["name"], char.get("photo"),
                     json.dumps(char.get("attributes", {}), ensure_ascii=False)),
                )
        # Insert pending custom chars created during new-game modal
        for nc in new_chars:
            nc_name = nc["name"].strip()[:80]
            nc_id = secrets.token_hex(6)
            nc_attrs = {k: str(v)[:120] for k, v in nc.get("attributes", {}).items()
                        if isinstance(k, str) and isinstance(v, str)}
            conn.execute(
                "INSERT INTO game_characters (id, deployment_id, name, photo_filename, attributes) VALUES (%s,%s,%s,%s,%s)",
                (nc_id, deploy_id, nc_name, None,
                 json.dumps(nc_attrs, ensure_ascii=False)),
            )
        conn.commit()
    return jsonify({"id": deploy_id, "url": f"/g/{deploy_id}"})


@app.get("/g/<deployment_id>")
def deployed_game(deployment_id: str) -> Response | str:
    with get_db() as conn:
        row = conn.execute("SELECT id FROM deployments WHERE id=%s", (deployment_id,)).fetchone()
    if row is None:
        return "Lien de jeu introuvable.", 404
    return render_template(
        "index.html",
        user_email=session.get("user_email", ""),
        show_login_modal=False,
        next_url="",
        deployment_id=deployment_id,
        show_admin=False,
    )


@app.get("/api/deployments")
def api_list_deployments() -> Response:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Connexion requise."}), 401
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, created_at FROM deployments WHERE user_email=%s ORDER BY created_at DESC",
            (email,),
        ).fetchall()
        result = []
        for row in rows:
            char_rows = conn.execute(
                "SELECT id, name FROM game_characters WHERE deployment_id=%s ORDER BY name",
                (row["id"],),
            ).fetchall()
            result.append({
                "id": row["id"],
                "url": f"/g/{row['id']}",
                "character_ids": [r["id"] for r in char_rows],
                "character_names": [r["name"] for r in char_rows],
                "created_at": row["created_at"],
            })
    return jsonify(result)


@app.patch("/api/deployments/<deploy_id>")
def api_update_deployment(deploy_id: str) -> Response:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Connexion requise."}), 401
    data = request.get_json(silent=True) or {}
    char_ids = data.get("character_ids")
    if not isinstance(char_ids, list) or not char_ids:
        return jsonify({"error": "Sélectionnez au moins un personnage."}), 400
    reload_characters()
    valid_ids = {c["id"] for c in CHARACTERS}
    char_ids = [cid for cid in char_ids if cid in valid_ids]
    if not char_ids:
        return jsonify({"error": "Aucun personnage valide."}), 400
    with get_db() as conn:
        row = conn.execute("SELECT user_email FROM deployments WHERE id=%s", (deploy_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Introuvable."}), 404
        if row["user_email"] != email:
            return jsonify({"error": "Accès refusé."}), 403
        conn.execute(
            "UPDATE deployments SET character_ids=%s WHERE id=%s",
            (json.dumps(char_ids), deploy_id),
        )
        # Sync game_characters: remove excluded, add new from global pool
        current_ids = {r["id"] for r in conn.execute(
            "SELECT id FROM game_characters WHERE deployment_id=%s", (deploy_id,)
        ).fetchall()}
        new_ids = set(char_ids)
        for removed_id in current_ids - new_ids:
            conn.execute(
                "DELETE FROM game_characters WHERE id=%s AND deployment_id=%s",
                (removed_id, deploy_id),
            )
        for added_id in new_ids - current_ids:
            char = CHARACTER_BY_ID.get(added_id)
            if char:
                conn.execute(
                    "INSERT INTO game_characters (id, deployment_id, name, photo_filename, attributes) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (added_id, deploy_id, char["name"], char.get("photo"),
                     json.dumps(char.get("attributes", {}), ensure_ascii=False)),
                )
        conn.commit()
    return jsonify({"success": True})


@app.delete("/api/deployments/<deploy_id>")
def api_delete_deployment(deploy_id: str) -> Response:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Connexion requise."}), 401
    with get_db() as conn:
        row = conn.execute("SELECT user_email FROM deployments WHERE id=%s", (deploy_id,)).fetchone()
        if row is None:
            return jsonify({"error": "Introuvable."}), 404
        if row["user_email"] != email:
            return jsonify({"error": "Accès refusé."}), 403
        conn.execute("DELETE FROM deployments WHERE id=%s", (deploy_id,))
        conn.commit()
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True)