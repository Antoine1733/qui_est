import pathlib

path = pathlib.Path("templates/admin.html")
src = path.read_text(encoding="utf-8")

# ── 1. Keep <head> up to opening <body> ────────────────────────────────────
head_end = src.index("  <body>") + len("  <body>")
head = src[:head_end]

# ── 2. Keep <script> block to end of file ─────────────────────────────────
script_start = src.index("\n    <script>")
script_block = src[script_start:]

# ── 3. Remove chars-modal JS listeners from script block ──────────────────
OLD_CHARS_JS = """
      // ── Gérer les personnages modal ───────────────────────────────────────
      document.getElementById('chars-open-btn').addEventListener('click', () => {
        document.getElementById('chars-modal').classList.add('open');
      });
      document.getElementById('chars-modal-close').addEventListener('click', () => {
        document.getElementById('chars-modal').classList.remove('open');
      });
      document.getElementById('chars-modal').addEventListener('click', e => {
        if (e.target === e.currentTarget) document.getElementById('chars-modal').classList.remove('open');
      });
"""
script_block = script_block.replace(OLD_CHARS_JS, "\n")

# ── 4. New body HTML ──────────────────────────────────────────────────────
new_body = r"""
    <!-- TOPBAR -->
    <div class="topbar">
      <a href="/">&#8592; Retour au jeu</a>
      <h1>Qui&nbsp;Est&nbsp;? &#8213; Mes Jeux</h1>
      <span style="color:#adb8c0;font-size:.82rem;margin-left:auto">{{ user_email }}</span>
      <button id="logout-btn" style="background:none;border:1px solid #adb8c0;color:#adb8c0;border-radius:6px;padding:4px 10px;cursor:pointer;font-family:inherit;font-size:.8rem">D&#233;connexion</button>
    </div>

    <!-- PAGE PRINCIPALE : liste des Jeux Qui Est -->
    <div class="page">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px;flex-wrap:wrap">
          <h2 style="margin:0">&#127918; Mes Jeux Qui&nbsp;Est</h2>
          <button id="deploy-new-btn" class="btn-submit" style="margin:0;padding:9px 18px;width:auto">+ Cr&#233;er un Jeu Qui Est</button>
        </div>
        <div id="deployment-list" style="display:flex;flex-direction:column;gap:10px">
          <p id="deployment-list-empty" style="font-size:.87rem;color:var(--muted);margin:0">Aucun Jeu Qui Est cr&#233;&#233; pour le moment.</p>
        </div>
      </div>
    </div>

    <!-- MODAL : Cr&#233;er / Modifier un Jeu (inclut la gestion des personnages) -->
    <div class="modal-backdrop" id="deploy-modal">
      <div class="modal" style="max-width:960px;width:100%">
        <button class="modal-close" id="deploy-modal-close">&#10005;</button>
        <h2 id="deploy-modal-title">&#10024; Cr&#233;er un Jeu Qui Est</h2>

        <!-- URL partageable (visible en modification ou apr&#232;s cr&#233;ation) -->
        <div id="deploy-modal-url-zone" style="display:none;margin-bottom:18px;padding:12px 14px;background:#f0fdf4;border-radius:8px;border:1.5px solid #a7f3d0">
          <div style="font-size:.8rem;color:#065f46;font-weight:700;margin-bottom:8px">&#128279; Lien partageable (ne change pas)</div>
          <div style="display:flex;gap:8px;align-items:center">
            <input type="text" id="deploy-modal-url" readonly style="flex:1;padding:7px 10px;border:1.5px solid #a7f3d0;border-radius:6px;font-size:.88rem;background:#fff" />
            <button id="deploy-modal-copy-btn" style="padding:7px 12px;background:var(--teal);color:#fff;border:none;border-radius:6px;cursor:pointer;font-family:inherit;font-size:.85rem;font-weight:700">Copier</button>
          </div>
        </div>

        <!-- Grille : liste personnages (gauche) | ajout personnage (droite) -->
        <div class="grid" style="margin-bottom:20px">

          <!-- Colonne gauche : personnages avec coches -->
          <div>
            <h3 style="margin:0 0 8px;font-size:.93rem;font-weight:800;color:var(--muted)">Personnages &#8213; coche ceux &#224; inclure</h3>
            <div class="char-list" id="char-list" style="max-height:calc(50vh - 60px)">
              {% for char in characters %}
              <div class="char-item" id="item-{{ char.id }}"
                   data-id="{{ char.id }}"
                   data-attrs='{{ char.attributes | tojson }}'
                   data-photo="{{ char.photo if char.get('photo') else '' }}">
                <input type="checkbox" class="deploy-modal-check" value="{{ char.id }}" checked
                       style="width:17px;height:17px;flex-shrink:0;cursor:pointer;accent-color:var(--accent)" />
                <img src="{{ '/photo/' + char.photo if char.get('photo') else '/avatar/' + char.id + '.svg' }}"
                     alt="{{ char.name }}" id="img-{{ char.id }}"
                     style="width:36px;height:36px;border-radius:7px;object-fit:cover;flex-shrink:0" />
                <div class="char-info">
                  <div class="char-name">
                    {{ char.name }}
                    <span class="char-badge {{ 'badge-preset' if char.is_preset else 'badge-custom' }}">
                      {{ 'pr&#233;' if char.is_preset else 'perso' }}
                    </span>
                  </div>
                  <div class="char-attrs">{{ char.attributes | tojson | truncate(60) }}</div>
                </div>
                <div class="item-actions">
                  <button class="btn-icon btn-edit" data-id="{{ char.id }}" title="Modifier attributs">&#9999;&#65039;</button>
                  <button class="btn-icon btn-delete" data-id="{{ char.id }}" title="Supprimer">&#128465;</button>
                </div>
              </div>
              {% else %}
              <p class="empty">Aucun personnage pour l'instant.</p>
              {% endfor %}
            </div>
          </div>

          <!-- Colonne droite : formulaire d'ajout -->
          <div>
            <h3 style="margin:0 0 8px;font-size:.93rem;font-weight:800;color:var(--muted)">Ajouter un personnage</h3>
            <form id="add-form" enctype="multipart/form-data">
              <div class="field">
                <label for="add-name">Nom *</label>
                <input type="text" id="add-name" name="name" placeholder="ex : Marie" required maxlength="80" />
              </div>
              <div class="field">
                <label>Photo (PNG/JPG/WEBP, max 8 Mo)</label>
                <div class="photo-wrap">
                  <input type="file" name="photo" id="add-photo" accept=".png,.jpg,.jpeg,.webp" style="flex:1" />
                  <img id="add-photo-preview" class="photo-preview" alt="Aper&#231;u" />
                </div>
              </div>
              <div class="field">
                <label>Attributs</label>
                <div id="add-attrs"></div>
              </div>
              <button type="submit" class="btn-submit" id="add-submit">Ajouter le personnage</button>
              <div class="feedback" id="add-feedback"></div>
            </form>
          </div>

        </div>

        <!-- Pied de modal : erreur + bouton sauvegarder -->
        <div style="border-top:1.5px solid #e4ddd5;padding-top:14px">
          <div id="deploy-modal-error" style="display:none;margin-bottom:8px;color:#dc2626;font-size:.88rem"></div>
          <button class="btn-submit" id="deploy-modal-save-btn" style="margin:0">&#128279; Cr&#233;er le Jeu Qui Est</button>
        </div>
      </div>
    </div>

    <!-- MODAL : Modifier les attributs d'un personnage -->
    <div class="modal-backdrop" id="edit-modal">
      <div class="modal" style="z-index:200">
        <button class="modal-close" id="modal-close">&#10005;</button>
        <h2 id="modal-title">Modifier</h2>
        <form id="edit-form" enctype="multipart/form-data">
          <input type="hidden" id="edit-id" />
          <div class="field">
            <label>Photo (laisser vide pour conserver l'actuelle)</label>
            <div class="photo-wrap">
              <input type="file" name="photo" id="edit-photo" accept=".png,.jpg,.jpeg,.webp" style="flex:1" />
              <img id="edit-photo-preview" class="photo-preview" alt="Aper&#231;u actuel" />
            </div>
          </div>
          <div class="field">
            <label>Attributs</label>
            <div id="edit-attrs"></div>
          </div>
          <button type="submit" class="btn-submit" id="edit-submit">Enregistrer les modifications</button>
          <div class="feedback" id="edit-feedback"></div>
        </form>
      </div>
    </div>
"""

result = head + new_body + script_block
path.write_text(result, encoding="utf-8")
print("Done —", len(result), "chars")
