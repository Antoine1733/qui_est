import pathlib

path = pathlib.Path("templates/admin.html")
html = path.read_text(encoding="utf-8")

# Keep <head> up to and including <body>
head_end = html.index("  <body>") + len("  <body>")
head = html[:head_end]

# Keep <script> block to end
script_start = html.index("\n    <script>")
script_and_end = html[script_start:]

new_body = """
    <div class="topbar">
      <a href="/">&#8592; Retour au jeu</a>
      <h1>Qui&nbsp;Est&nbsp;? &#8212; Mes Jeux</h1>
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

    <!-- MODAL DEPLOY : Cr&#233;er / Modifier un Jeu (contient aussi la gestion des personnages) -->
    <div class="modal-backdrop" id="deploy-modal">
      <div class="modal" style="max-width:960px;width:100%">
        <button class="modal-close" id="deploy-modal-close">&#10005;</button>
        <h2 id="deploy-modal-title">&#10024; Cr&#233;er un Jeu Qui Est</h2>

        <!-- Lien (visible en mode modification) -->
        <div id="deploy-modal-url-zone" style="display:none;margin-bottom:18px;padding:12px 14px;background:#f0fdf4;border-radius:8px;border:1.5px solid #a7f3d0">
          <div style="font-size:.8rem;color:#065f46;font-weight:700;margin-bottom:8px">&#128279; Lien partageable (ne change pas)</div>
          <div style="display:flex;gap:8px;align-items:center">
            <input type="text" id="deploy-modal-url" readonly style="flex:1;padding:7px 10px;border:1.5px solid #a7f3d0;border-radius:6px;font-size:.88rem;background:#fff" />
            <button id="deploy-modal-copy-btn" style="padding:7px 12px;background:var(--teal);color:#fff;border:none;border-radius:6px;cursor:pointer;font-family:inherit;font-size:.85rem;font-weight:700">Copier</button>
          </div>
        </div>

        <!-- Grille : liste persos | formulaire ajout -->
        <div class="grid" style="margin-bottom:20px">

          <!-- Colonne gauche : liste des personnages avec coches -->
          <div>
            <h3 style="margin:0 0 10px;font-size:.95rem;font-weight:800;color:var(--muted)">Personnages &#8212; coche ceux &#224; inclure</h3>
            <div class="char-list" id="char-list" style="max-height:50vh">
              {% for char in characters %}
              <div class="char-item" id="item-{{ char.id }}"
                   data-id="{{ char.id }}"
                   data-attrs='{{ char.attributes | tojson }}'
                   data-photo="{{ char.photo if char.get('photo') else '' }}">
                <input type="checkbox" class="deploy-modal-check" value="{{ char.id }}" checked
                       style="width:18px;height:18px;flex-shrink:0;cursor:pointer;accent-color:var(--accent)" />
                <img src="{{ '/photo/' + char.photo if char.get('photo') else '/avatar/' + char.id + '.svg' }}"
                     alt="{{ char.name }}" id="img-{{ char.id }}" style="width:36px;height:36px;border-radius:7px;object-fit:cover;flex-shrink:0" />
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
                  <button class="btn-icon btn-edit" data-id="{{ char.id }}" title="Modifier">&#9999;&#65039;</button>
                  <button class="btn-icon btn-delete" data-id="{{ char.id }}" title="Supprimer">&#128465;</button>
                </div>
              </div>
              {% else %}
              <p class="empty">Aucun personnage pour l'instant.</p>
              {% endfor %}
            </div>
          </div>

          <!-- Colonne droite : ajouter un personnage -->
          <div>
            <h3 style="margin:0 0 10px;font-size:.95rem;font-weight:800;color:var(--muted)">Ajouter un personnage</h3>
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

        <!-- Bouton sauvegarder le jeu -->
        <div style="border-top:1.5px solid #e4ddd5;padding-top:16px">
          <div id="deploy-modal-error" style="display:none;margin-bottom:8px;color:#dc2626;font-size:.88rem"></div>
          <button class="btn-submit" id="deploy-modal-save-btn" style="margin:0">&#128279; Cr&#233;er le Jeu Qui Est</button>
        </div>
      </div>
    </div>

    <!-- MODAL : Modifier les attributs d'un personnage -->
    <div class="modal-backdrop" id="edit-modal">
      <div class="modal">
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

# Build JS addition: open deploy modal on page load / new btn, remove chars-modal logic
chars_js = """
      // ── Ouvrir la modal deploy ────────────────────────────────────────────
      document.getElementById('deploy-new-btn').addEventListener('click', () => openDeployModal(null));
      document.getElementById('deploy-modal-close').addEventListener('click', closeDeployModal);
      document.getElementById('deploy-modal').addEventListener('click', e => {
        if (e.target === e.currentTarget) closeDeployModal();
      });

"""

result = head + new_body + script_and_end

# Replace old chars-modal JS block
old_chars_js = """
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
result = result.replace(old_chars_js, chars_js)

# Also remove the deploy-new-btn and deploy-modal-close listeners that were already in the Jeux Qui Est block
old_listeners = "      document.getElementById('deploy-new-btn').addEventListener('click', () => openDeployModal(null));\n      document.getElementById('deploy-modal-close').addEventListener('click', closeDeployModal);\n      document.getElementById('deploy-modal').addEventListener('click', e => { if (e.target === e.currentTarget) closeDeployModal(); });\n"
result = result.replace(old_listeners, "")

path.write_text(result, encoding="utf-8")
print("OK — admin.html rebuilt")
