const state = {
  characters: [],
  eliminated: new Set(),
  gameFinished: false,
  revealedSecretId: null,
  isLoading: false,
  questionCount: 0,
};

const elements = {
  board: document.getElementById("board"),
  chatLog: document.getElementById("chat-log"),
  status: document.getElementById("status"),
  remainingCount: document.getElementById("remaining-count"),
  remainingCount2: document.getElementById("remaining-count-2"),
  newGameButton: document.getElementById("new-game-btn"),
  playAgainButton: document.getElementById("play-again-btn"),
  questionForm: document.getElementById("question-form"),
  questionInput: document.getElementById("question-input"),
  askButton: document.getElementById("ask-btn"),
  result: document.getElementById("result"),
  scrollArea: document.getElementById("scroll-area"),
  boardDrawer: document.getElementById("board-drawer"),
  boardBackdrop: document.getElementById("board-backdrop"),
  boardToggleBtn: document.getElementById("board-toggle-btn"),
  boardCloseBtn: document.getElementById("board-close-btn"),
  drawerResultOverlay: document.getElementById("drawer-result-overlay"),
  droEmoji: document.getElementById("dro-emoji"),
  droMessage: document.getElementById("dro-message"),
  droName: document.getElementById("dro-name"),
  droReplayBtn: document.getElementById("dro-replay-btn"),
};

function openDrawer() {
  elements.boardDrawer.classList.add("is-open");
  elements.boardBackdrop.classList.add("is-open");
  elements.boardDrawer.setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  elements.boardDrawer.classList.remove("is-open");
  elements.boardBackdrop.classList.remove("is-open");
  elements.boardDrawer.setAttribute("aria-hidden", "true");
}

function setStatus(text) {
  elements.status.textContent = text;
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const ROLE_LABELS = { toi: "Vous", ordi: "Arbitre", system: "Système" };

function scrollToBottom() {
  requestAnimationFrame(() => {
    const el = elements.scrollArea;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  });
}

function addMessage(role, text, questionNumber = null) {
  const item = document.createElement("article");
  item.className = `msg msg-${role}`;
  const label = ROLE_LABELS[role] ?? role.toUpperCase();
  const badge = (role === "toi" && questionNumber !== null)
    ? ` <span class="msg-qnum">#${parseInt(questionNumber, 10)}</span>`
    : "";
  item.innerHTML = `<span class="msg-role">${escapeHtml(label)}${badge}</span><p>${escapeHtml(text)}</p>`;
  elements.chatLog.appendChild(item);
  scrollToBottom();
}

function remainingCharacters() {
  return state.characters.filter((character) => !state.eliminated.has(character.id));
}

function updateRemaining() {
  const count = String(remainingCharacters().length);
  elements.remainingCount.textContent = count;
  elements.remainingCount2.textContent = count;
}

function setLoading(isLoading) {
  state.isLoading = isLoading;
  elements.newGameButton.disabled = isLoading;

  const askDisabled =
    isLoading ||
    state.characters.length === 0 ||
    state.gameFinished;
  elements.askButton.disabled = askDisabled;
}

function renderBoard() {
  const fragment = document.createDocumentFragment();

  state.characters.forEach((character, index) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "character-card";
    card.style.setProperty("--order", String(index));

    if (state.eliminated.has(character.id)) {
      card.classList.add("is-eliminated");
    }

    if (state.revealedSecretId === character.id) {
      card.classList.add("is-secret");
      if (state.gameFinished) {
        card.classList.add("is-winner");
      }
    }

    const photo = document.createElement("div");
    photo.className = "photo-wrap";

    const image = document.createElement("img");
    image.src = character.photoUrl;
    image.alt = `Portrait de ${character.name}`;
    photo.appendChild(image);

    const name = document.createElement("h3");
    name.textContent = character.name;

    const chip = document.createElement("p");
    chip.className = "chip";
    chip.textContent = state.eliminated.has(character.id) ? "Baisse" : "Actif";

    card.appendChild(photo);
    card.appendChild(name);
    card.appendChild(chip);

    card.addEventListener("click", () => {
      if (state.gameFinished || state.isLoading) {
        return;
      }
      toggleCharacter(character.id);
    });

    fragment.appendChild(card);
  });

  elements.board.replaceChildren(fragment);
  updateRemaining();
}

function toggleCharacter(characterId) {
  if (state.eliminated.has(characterId)) {
    state.eliminated.delete(characterId);
  } else {
    state.eliminated.add(characterId);
  }

  renderBoard();

  const remaining = remainingCharacters();
  if (remaining.length === 0) {
    setStatus("Aucune carte restante. Releve au moins une photo.");
    return;
  }

  if (remaining.length === 1) {
    verifyLastCharacter(remaining[0].id);
    return;
  }

  setStatus(`Encore ${remaining.length} profils possibles.`);
}

async function startNewGame() {
  setLoading(true);
  const cfg = window.GAME_CONFIG || {};
  const url = cfg.deploymentId
    ? `/api/new-game?d=${encodeURIComponent(cfg.deploymentId)}`
    : "/api/new-game";

  try {
    const response = await fetch(url);
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Impossible de lancer la partie");
    }

    state.characters = data.characters;
    state.eliminated = new Set();
    state.gameFinished = false;
    state.revealedSecretId = null;

    elements.chatLog.innerHTML = "";
    elements.result.textContent = "";
    elements.result.classList.add("hidden");
    elements.playAgainButton.classList.add("hidden");
    elements.scrollArea.scrollTo({ top: 0, behavior: "instant" });
    state.questionCount = 0;

    // Reset drawer overlay
    elements.drawerResultOverlay.classList.add("hidden");
    elements.drawerResultOverlay.classList.remove("is-win", "is-lose");

    setStatus("Partie lancee. Pose une question puis elimine des cartes.");
    addMessage("system", "Nouvelle partie prete. Bonne chance.");
    addMessage("ordi", "J'ai choisi un personnage secret. Appuie sur \"Photos\" pour voir et \u00e9liminer des profils en cliquant dessus. Pose-moi des questions pour affiner. Quand il n'en reste qu'un seul actif, tu sauras si tu as gagn\u00e9 !");
    renderBoard();
  } catch (error) {
    setStatus(`Erreur: ${error.message}`);
  } finally {
    setLoading(false);
  }
}

async function askQuestion(event) {
  event.preventDefault();

  const question = elements.questionInput.value.trim();
  if (!question || state.characters.length === 0 || state.gameFinished) {
    return;
  }

  const optimisticNum = state.questionCount + 1;
  addMessage("toi", question, optimisticNum);
  elements.questionInput.value = "";
  setLoading(true);

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Echec de la question");
    }

    const qNum = data.question_number ?? null;
    state.questionCount = qNum ?? state.questionCount;

    if (data.notice) {
      addMessage("ordi", data.notice);
      setStatus("Analyse de la photo en cours...");
      await new Promise((resolve) => window.setTimeout(resolve, 900));
    }

    const providerSuffix = data.provider === "fallback" ? " (mode local)" : "";
    addMessage("ordi", `${data.answer}${providerSuffix}`);
    setStatus(`Question ${state.questionCount} — continue à éliminer des profils.`);
  } catch (error) {
    addMessage("system", `Erreur: ${error.message}`);
    setStatus("La question n'a pas pu etre traitee.");
  } finally {
    setLoading(false);
    elements.questionInput.focus();
  }
}

async function verifyLastCharacter(guessId) {
  if (state.gameFinished) {
    return;
  }

  state.gameFinished = true;
  setLoading(true);
  setStatus("Verification finale...");

  try {
    const response = await fetch("/api/final-check", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ guess_id: guessId }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Echec de la verification finale");
    }

    state.revealedSecretId = data.secret.id;
    renderBoard();

    // Afficher l'overlay résultat dans le drawer
    const isWin = data.success;
    const qCount = data.question_count ?? state.questionCount;
    const plural = qCount > 1 ? "s" : "";
    const scoreText = isWin
      ? `En ${qCount} question${plural} !`
      : `C'était ${data.secret.name} — ${qCount} question${plural}.`;
    elements.droEmoji.textContent = isWin ? "🎉" : "😢";
    elements.droMessage.textContent = isWin ? `Bravo ! C'était ${data.secret.name}` : "Perdu !";
    elements.droName.textContent = scoreText;
    elements.drawerResultOverlay.classList.remove("hidden", "is-win", "is-lose");
    elements.drawerResultOverlay.classList.add(isWin ? "is-win" : "is-lose");

    // Ouvrir le drawer pour que l'animation soit visible
    window.setTimeout(() => openDrawer(), 400);

    elements.result.classList.add("hidden");
    elements.playAgainButton.classList.remove("hidden");
    scrollToBottom();

    setStatus(isWin ? "Bravo ! Lance une nouvelle partie." : `Perdu. C'était ${data.secret.name}.`);
    addMessage("system", `${data.message} C'était ${data.secret.name}.`);
  } catch (error) {
    state.gameFinished = false;
    setStatus(`Erreur: ${error.message}`);
  } finally {
    setLoading(false);
  }
}

elements.questionForm.addEventListener("submit", askQuestion);
elements.newGameButton.addEventListener("click", startNewGame);
elements.playAgainButton.addEventListener("click", startNewGame);
elements.droReplayBtn.addEventListener("click", startNewGame);
elements.boardToggleBtn.addEventListener("click", openDrawer);
elements.boardCloseBtn.addEventListener("click", closeDrawer);
elements.boardBackdrop.addEventListener("click", closeDrawer);

startNewGame();

// ── Login modal ──────────────────────────────────────────────────
(function () {
  const cfg = window.GAME_CONFIG || {};
  const backdrop = document.getElementById('login-backdrop');
  if (!backdrop) return;

  if (cfg.showLoginModal) {
    backdrop.classList.remove('hidden');
    setTimeout(() => document.getElementById('login-email-input')?.focus(), 100);
  }

  async function doLogin() {
    const email = document.getElementById('login-email-input').value.trim();
    const errorEl = document.getElementById('login-error');
    errorEl.textContent = '';
    if (!email || !email.includes('@')) {
      errorEl.textContent = 'Adresse e-mail invalide.';
      return;
    }
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();
      if (res.ok) {
        backdrop.classList.add('hidden');
        if (cfg.nextUrl) window.location.href = cfg.nextUrl;
      } else {
        errorEl.textContent = data.error || 'Erreur.';
      }
    } catch {
      document.getElementById('login-error').textContent = 'Erreur réseau.';
    }
  }

  document.getElementById('login-submit-btn').addEventListener('click', doLogin);
  document.getElementById('login-email-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doLogin();
  });
  document.getElementById('login-skip-btn').addEventListener('click', () => {
    backdrop.classList.add('hidden');
  });
})();
