import pathlib

path = pathlib.Path("templates/admin.html")
src = path.read_text(encoding="utf-8")

# ── Keep <head> CSS (everything up to and including </style>\n  </head>\n  <body>)
head_end = src.index("  <body>") + len("  <body>")
head = src[:head_end]

# ── Inject extra CSS before </style>
extra_css = """
      /* Staged-delete state */
      .char-item.pending-delete { background: #fee2e2; opacity: .7; }
      .char-item.pending-delete .char-name { text-decoration: line-through; color: var(--muted); }
      .btn-undo { background: none; border: 1px solid #fca5a5; color: var(--danger); border-radius: 6px; padding: 2px 8px; cursor: pointer; font-size: .75rem; font-weight: 700; white-space: nowrap; }
      /* Checkbox accent */
      .char-include-check { width: 18px; height: 18px; flex-shrink: 0; cursor: pointer; accent-color: var(--accent); }
"""
head = head.replace("    </style>\n  </head>", extra_css + "    </style>\n  </head>")

# ── Keep <script> block to end
script_start = src.index("\n    <script>")
script_block = src[script_start:]

# ── New body HTML ──────────────────────────────────────────────────────────────
new_body = """
    <!-- TOPBAR -->
    <div class="topbar">
      <a href="/">&#8592; Retour au jeu</a>
      <h1>Qui&nbsp;Est&nbsp;? &#8213; Mes Jeux</h1>
      <span style="color:#adb8c0;font-size:.82rem;margin-left:auto">{{ user_email }}</span>
      <button id="logout-btn" style="background:none;border:1px solid #adb8c0;color:#adb8c0;border-radius:6px;padding:4px 10px;cursor:pointer;font-family:inherit;font-size:.8rem">D&#233;connexion</button>
    </div>

    <!-- PAGE PRINCIPALE -->
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

    <!-- MODAL : Cr&#233;er / Modifier un Jeu -->
    <div class="modal-backdrop" id="deploy-modal">
      <div class="modal" style="max-width:960px;width:100%">
        <button class="modal-close" id="deploy-modal-close">&#10005;</button>
        <h2 id="deploy-modal-title">&#10024; Cr&#233;er un Jeu Qui Est</h2>

        <!-- Lien partageable -->
        <div id="deploy-modal-url-zone" style="display:none;margin-bottom:18px;padding:12px 14px;background:#f0fdf4;border-radius:8px;border:1.5px solid #a7f3d0">
          <div style="font-size:.8rem;color:#065f46;font-weight:700;margin-bottom:8px">&#128279; Lien partageable (ne change pas)</div>
          <div style="display:flex;gap:8px;align-items:center">
            <input type="text" id="deploy-modal-url" readonly style="flex:1;padding:7px 10px;border:1.5px solid #a7f3d0;border-radius:6px;font-size:.88rem;background:#fff" />
            <button id="deploy-modal-copy-btn" style="padding:7px 12px;background:var(--teal);color:#fff;border:none;border-radius:6px;cursor:pointer;font-family:inherit;font-size:.85rem;font-weight:700">Copier</button>
          </div>
        </div>

        <!-- Grille : liste personnages (gauche) | ajout personnage (droite) -->
        <div class="grid" style="margin-bottom:20px;align-items:stretch">

          <!-- Colonne gauche : personnages avec cases à cocher -->
          <div style="display:flex;flex-direction:column;min-height:0">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;flex-shrink:0;flex-wrap:wrap">
              <h3 style="margin:0;font-size:.93rem;font-weight:800;color:var(--muted)">Personnages disponibles</h3>
              <button id="restore-presets-btn" title="R&#233;tablir tous les personnages pr&#233;enregistr&#233;s supprim&#233;s"
                      style="background:none;border:1px solid var(--teal);color:var(--teal);border-radius:6px;padding:3px 9px;cursor:pointer;font-family:inherit;font-size:.75rem;font-weight:700">
                &#8635; R&#233;tablir pr&#233;enregistr&#233;s
              </button>
            </div>
            <p style="font-size:.8rem;color:var(--muted);margin:0 0 8px;flex-shrink:0">Coche pour inclure dans le jeu</p>
            <div class="char-list" id="char-list" style="flex:1;height:0;max-height:none">
              {% for char in characters %}
              <div class="char-item" id="item-{{ char.id }}"
                   data-id="{{ char.id }}"
                   data-preset="{{ '1' if char.is_preset else '0' }}"
                   data-attrs='{{ char.attributes | tojson }}'
                   data-photo="{{ char.photo if char.get('photo') else '' }}">
                <input type="checkbox" class="char-include-check" value="{{ char.id }}" />
                <img src="{{ '/photo/' + char.photo if char.get('photo') else '/avatar/' + char.id + '.svg' }}"
                     alt="{{ char.name }}" id="img-{{ char.id }}"
                     style="width:36px;height:36px;border-radius:7px;object-fit:cover;flex-shrink:0" />
                <div class="char-info">
                  <div class="char-name">
                    {{ char.name }}
                    <span class="char-badge {{ 'badge-preset' if char.is_preset else 'badge-custom' }}">
                      {{ 'pr\u00e9enregistr\u00e9' if char.is_preset else 'perso' }}
                    </span>
                  </div>
                  <div class="char-attrs">{{ char.attributes | tojson | truncate(60) }}</div>
                </div>
                <div class="item-actions">
                  <button class="btn-icon btn-edit" data-id="{{ char.id }}" title="Modifier attributs">&#9999;&#65039;</button>
                  <button class="btn-icon btn-delete" data-id="{{ char.id }}" title="Supprimer d\u00e9finitivement">&#128465;</button>
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
                  <img id="add-photo-preview" class="photo-preview" alt="Aper\u00e7u" />
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

        <!-- Pied de modal -->
        <div style="border-top:1.5px solid #e4ddd5;padding-top:14px">
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
              <img id="edit-photo-preview" class="photo-preview" alt="Aper\u00e7u actuel" />
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

# ── New JS block ───────────────────────────────────────────────────────────────
new_script = r"""
    <script>
      // ── Standard attributes ────────────────────────────────────────
      const STANDARD_ATTRS = [
        "genre", "cheveux", "yeux", "lunettes", "barbe", "moustache", "chapeau",
        "taille", "corpulence", "coupe de cheveux", "type de cheveux",
        "teint", "sourcils", "nez", "levres"
      ];
      const ATTR_PLACEHOLDERS = {
        "genre":"ex : femme","cheveux":"ex : blond","yeux":"ex : bleu",
        "lunettes":"ex : non","barbe":"ex : non","moustache":"ex : non",
        "chapeau":"ex : non","taille":"ex : grand","corpulence":"ex : mince",
        "coupe de cheveux":"ex : long","type de cheveux":"ex : bouclé",
        "teint":"ex : clair","sourcils":"ex : épais","nez":"ex : retroussé","levres":"ex : épaisses",
      };

      function buildAttrFields(containerId, values = {}) {
        const container = document.getElementById(containerId);
        container.innerHTML = '';
        const grid = document.createElement('div');
        grid.className = 'field-row';
        grid.style.gridTemplateColumns = '1fr 1fr';
        STANDARD_ATTRS.forEach(key => {
          const wrap = document.createElement('div');
          wrap.className = 'field'; wrap.style.marginBottom = '8px';
          const lbl = document.createElement('label'); lbl.textContent = key;
          const inp = document.createElement('input');
          inp.type = 'text'; inp.dataset.attrKey = key;
          inp.placeholder = ATTR_PLACEHOLDERS[key] || ''; inp.value = values[key] || ''; inp.maxLength = 120;
          wrap.appendChild(lbl); wrap.appendChild(inp); grid.appendChild(wrap);
        });
        container.appendChild(grid);
      }

      function collectAttrs(containerId) {
        const attrs = {};
        document.querySelectorAll(`#${containerId} input[data-attr-key]`).forEach(inp => {
          attrs[inp.dataset.attrKey] = inp.value.trim();
        });
        return attrs;
      }
      buildAttrFields('add-attrs');

      // ── Photo preview ──────────────────────────────────────────────
      function bindPhotoPreview(inputId, previewId) {
        document.getElementById(inputId).addEventListener('change', function() {
          const p = document.getElementById(previewId);
          if (this.files[0]) { const r = new FileReader(); r.onload = e => { p.src = e.target.result; p.style.display = 'block'; }; r.readAsDataURL(this.files[0]); }
          else { p.style.display = 'none'; }
        });
      }
      bindPhotoPreview('add-photo', 'add-photo-preview');
      bindPhotoPreview('edit-photo', 'edit-photo-preview');

      // ── Staged deletes ─────────────────────────────────────────────
      // IDs pending deletion from DB (executed only when user saves)
      const pendingDeletes = new Set();

      function stageDelete(id) {
        pendingDeletes.add(id);
        const item = document.getElementById(`item-${id}`);
        if (!item) return;
        item.classList.add('pending-delete');
        // Replace delete btn with undo btn
        const delBtn = item.querySelector('.btn-delete');
        if (delBtn) {
          const undo = document.createElement('button');
          undo.className = 'btn-undo'; undo.textContent = 'Annuler'; undo.type = 'button';
          undo.addEventListener('click', () => unstageDelete(id));
          delBtn.replaceWith(undo);
        }
      }

      function unstageDelete(id) {
        pendingDeletes.delete(id);
        const item = document.getElementById(`item-${id}`);
        if (!item) return;
        item.classList.remove('pending-delete');
        const undoBtn = item.querySelector('.btn-undo');
        if (undoBtn) {
          const delBtn = document.createElement('button');
          delBtn.className = 'btn-icon btn-delete'; delBtn.dataset.id = id;
          delBtn.title = 'Supprimer définitivement'; delBtn.innerHTML = '🗑';
          delBtn.addEventListener('click', () => stageDelete(id));
          undoBtn.replaceWith(delBtn);
        }
      }

      function clearStagedDeletes() {
        [...pendingDeletes].forEach(id => unstageDelete(id));
        // pendingDeletes is now empty (unstageDelete clears each)
      }

      // ── Edit / insert helpers ──────────────────────────────────────
      function bindEditBtn(btn) {
        btn.addEventListener('click', () => {
          const id = btn.dataset.id;
          const item = document.getElementById(`item-${id}`);
          const name = item.querySelector('.char-name').firstChild.textContent.trim();
          const attrs = JSON.parse(item.dataset.attrs || '{}');
          const photo = item.dataset.photo;
          document.getElementById('edit-id').value = id;
          document.getElementById('modal-title').textContent = `Modifier \u2014 ${name}`;
          buildAttrFields('edit-attrs', attrs);
          const preview = document.getElementById('edit-photo-preview');
          preview.src = photo ? `/photo/${photo}` : `/avatar/${id}.svg`;
          preview.style.display = 'block';
          document.getElementById('edit-photo').value = '';
          showFeedback('edit-feedback', null);
          document.getElementById('edit-modal').classList.add('open');
        });
      }

      function insertCharItem(char, checked = false) {
        const list = document.getElementById('char-list');
        const empty = list.querySelector('.empty');
        if (empty) empty.remove();
        const photoSrc = char.photo ? `/photo/${char.photo}` : `/avatar/${char.id}.svg`;
        const attrsStr = JSON.stringify(char.attributes || {});
        const attrsPreview = attrsStr.length > 60 ? attrsStr.slice(0, 57) + '...' : attrsStr;
        const item = document.createElement('div');
        item.className = 'char-item';
        item.id = `item-${char.id}`;
        item.dataset.id = char.id;
        item.dataset.preset = char.is_preset ? '1' : '0';
        item.dataset.attrs = attrsStr;
        item.dataset.photo = char.photo || '';
        item.innerHTML = `
          <input type="checkbox" class="char-include-check" value="${char.id}"${checked ? ' checked' : ''} />
          <img src="${photoSrc}" alt="${char.name}" id="img-${char.id}"
               style="width:36px;height:36px;border-radius:7px;object-fit:cover;flex-shrink:0" />
          <div class="char-info">
            <div class="char-name">${char.name} <span class="char-badge ${char.is_preset ? 'badge-preset' : 'badge-custom'}">${char.is_preset ? 'préenregistré' : 'perso'}</span></div>
            <div class="char-attrs">${attrsPreview}</div>
          </div>
          <div class="item-actions">
            <button class="btn-icon btn-edit" data-id="${char.id}" title="Modifier attributs">✏️</button>
            <button class="btn-icon btn-delete" data-id="${char.id}" title="Supprimer définitivement">🗑</button>
          </div>`;
        bindEditBtn(item.querySelector('.btn-edit'));
        item.querySelector('.btn-delete').addEventListener('click', () => stageDelete(char.id));
        list.appendChild(item);
      }

      // ── Add form ───────────────────────────────────────────────────
      document.getElementById('add-form').addEventListener('submit', async function(e) {
        e.preventDefault();
        const btn = document.getElementById('add-submit');
        btn.disabled = true;
        showFeedback('add-feedback', null);
        const formData = new FormData(this);
        formData.set('attributes', JSON.stringify(collectAttrs('add-attrs')));
        try {
          const res = await fetch('/api/admin/characters', { method: 'POST', body: formData });
          const data = await res.json();
          if (res.ok) {
            showFeedback('add-feedback', 'success', `✓ ${data.name} ajouté !`);
            this.reset();
            document.getElementById('add-photo-preview').style.display = 'none';
            buildAttrFields('add-attrs');
            insertCharItem(data, false); // unchecked by default — user decides
          } else { showFeedback('add-feedback', 'error', `✗ ${data.error || 'Erreur'}`); }
        } catch { showFeedback('add-feedback', 'error', '✗ Erreur réseau.'); }
        finally { btn.disabled = false; }
      });

      // ── Bind initial buttons ───────────────────────────────────────
      document.querySelectorAll('.btn-edit').forEach(bindEditBtn);
      document.querySelectorAll('.btn-delete').forEach(btn => {
        btn.addEventListener('click', () => stageDelete(btn.dataset.id));
      });

      // ── Edit modal ─────────────────────────────────────────────────
      document.getElementById('modal-close').addEventListener('click', closeModal);
      document.getElementById('edit-modal').addEventListener('click', e => { if (e.target === e.currentTarget) closeModal(); });
      function closeModal() { document.getElementById('edit-modal').classList.remove('open'); }

      document.getElementById('edit-form').addEventListener('submit', async function(e) {
        e.preventDefault();
        const id = document.getElementById('edit-id').value;
        const btn = document.getElementById('edit-submit');
        btn.disabled = true; showFeedback('edit-feedback', null);
        const formData = new FormData(this);
        formData.set('attributes', JSON.stringify(collectAttrs('edit-attrs')));
        try {
          const res = await fetch(`/api/admin/characters/${encodeURIComponent(id)}/edit`, { method: 'POST', body: formData });
          const data = await res.json();
          if (res.ok) {
            const item = document.getElementById(`item-${id}`);
            if (item) {
              const attrsStr = JSON.stringify(data.attributes || {});
              item.dataset.attrs = attrsStr; item.dataset.photo = data.photo || '';
              const imgEl = item.querySelector('img');
              if (imgEl) imgEl.src = data.photo ? `/photo/${data.photo}` : `/avatar/${id}.svg`;
              const attrsEl = item.querySelector('.char-attrs');
              if (attrsEl) attrsEl.textContent = attrsStr.length > 60 ? attrsStr.slice(0, 57) + '...' : attrsStr;
            }
            closeModal();
          } else { showFeedback('edit-feedback', 'error', `✗ ${data.error || 'Erreur'}`); }
        } catch { showFeedback('edit-feedback', 'error', '✗ Erreur réseau.'); }
        finally { btn.disabled = false; }
      });

      // ── Restore presets ────────────────────────────────────────────
      document.getElementById('restore-presets-btn').addEventListener('click', async function() {
        this.disabled = true;
        try {
          const res = await fetch('/api/admin/presets/restore', { method: 'POST' });
          if (res.ok) { location.reload(); }
          else { alert('Erreur lors de la restauration.'); this.disabled = false; }
        } catch { alert('Erreur réseau.'); this.disabled = false; }
      });

      // ── Logout ────────────────────────────────────────────────────
      document.getElementById('logout-btn').addEventListener('click', async function() {
        await fetch('/api/auth/logout', { method: 'POST' });
        window.location.href = '/';
      });

      // ── Jeux Qui Est ──────────────────────────────────────────────
      let deployEditingId = null;

      function setupCheckboxes(activeIds) {
        // activeIds = Set of IDs to CHECK. All others unchecked.
        document.querySelectorAll('.char-include-check').forEach(cb => {
          cb.checked = activeIds.has(cb.value);
        });
      }

      function openDeployModal(d) {
        deployEditingId = d ? d.id : null;
        clearStagedDeletes();
        const errEl = document.getElementById('deploy-modal-error');
        const urlZone = document.getElementById('deploy-modal-url-zone');
        const saveBtn = document.getElementById('deploy-modal-save-btn');
        errEl.style.display = 'none';
        if (d) {
          document.getElementById('deploy-modal-title').textContent = '✏️ Modifier ce Jeu Qui Est';
          saveBtn.textContent = '💾 Enregistrer les modifications';
          saveBtn.disabled = false;
          document.getElementById('deploy-modal-url').value = window.location.origin + d.url;
          urlZone.style.display = 'block';
          // Cocher uniquement les personnages de CE jeu
          setupCheckboxes(new Set(d.character_ids || []));
        } else {
          document.getElementById('deploy-modal-title').textContent = '✨ Créer un Jeu Qui Est';
          saveBtn.textContent = '🔗 Créer le Jeu Qui Est';
          saveBtn.disabled = false;
          urlZone.style.display = 'none';
          // Cocher uniquement les préenregistrés par défaut
          const presetIds = new Set(
            [...document.querySelectorAll('#char-list .char-item')]
              .filter(el => el.dataset.preset === '1')
              .map(el => el.dataset.id)
          );
          setupCheckboxes(presetIds);
        }
        document.getElementById('deploy-modal').classList.add('open');
      }

      function closeDeployModal() {
        clearStagedDeletes();
        document.getElementById('deploy-modal').classList.remove('open');
      }

      async function loadDeployments() {
        try {
          const res = await fetch('/api/deployments');
          if (!res.ok) return;
          const deployments = await res.json();
          const list = document.getElementById('deployment-list');
          const empty = document.getElementById('deployment-list-empty');
          [...list.children].forEach(el => { if (el !== empty) el.remove(); });
          if (!deployments.length) { empty.style.display = 'block'; return; }
          empty.style.display = 'none';
          deployments.forEach(d => {
            const fullUrl = window.location.origin + d.url;
            const date = new Date(d.created_at + 'Z').toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
            const row = document.createElement('div');
            row.dataset.deployId = d.id;
            row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:12px 14px;background:#f8f7f5;border-radius:10px;border:1.5px solid #e4ddd5;flex-wrap:wrap';
            row.innerHTML =
              '<div style="flex:1;min-width:0">' +
                `<div style="font-size:.84rem;font-weight:700;color:#1e2930;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${fullUrl}">${fullUrl}</div>` +
                `<div style="font-size:.76rem;color:var(--muted);margin-top:3px">${d.character_names.join(', ') || '—'} · ${date}</div>` +
              '</div>' +
              `<button class="dl-copy-btn" data-url="${fullUrl}" style="padding:6px 12px;background:var(--teal);color:#fff;border:none;border-radius:7px;cursor:pointer;font-size:.82rem;font-weight:700;white-space:nowrap">Copier</button>` +
              `<button class="dl-edit-btn" style="padding:6px 12px;background:#fff;color:#1e2930;border:1.5px solid #d0c8be;border-radius:7px;cursor:pointer;font-size:.82rem;font-weight:700;white-space:nowrap">✏️ Modifier</button>` +
              `<button class="dl-del-btn" style="padding:6px 12px;background:#fee2e2;color:#dc2626;border:1.5px solid #fca5a5;border-radius:7px;cursor:pointer;font-size:.82rem;font-weight:700;white-space:nowrap">Supprimer</button>`;
            row.querySelector('.dl-copy-btn').addEventListener('click', function() {
              const url = this.dataset.url;
              if (navigator.clipboard) navigator.clipboard.writeText(url);
              else { const t = document.createElement('input'); t.value = url; document.body.appendChild(t); t.select(); document.execCommand('copy'); document.body.removeChild(t); }
              const orig = this.textContent; this.textContent = '✓ Copié !'; setTimeout(() => { this.textContent = orig; }, 2000);
            });
            row.querySelector('.dl-edit-btn').addEventListener('click', () => openDeployModal(d));
            row.querySelector('.dl-del-btn').addEventListener('click', async function() {
              if (!confirm('Supprimer ce Jeu Qui Est ? Les personnes qui ont le lien ne pourront plus jouer.')) return;
              const r = await fetch('/api/deployments/' + d.id, { method: 'DELETE' });
              if (r.ok) { row.remove(); if (!list.querySelectorAll('[data-deploy-id]').length) empty.style.display = 'block'; }
            });
            list.appendChild(row);
          });
        } catch(e) { console.error(e); }
      }
      loadDeployments();

      document.getElementById('deploy-new-btn').addEventListener('click', () => openDeployModal(null));
      document.getElementById('deploy-modal-close').addEventListener('click', closeDeployModal);
      document.getElementById('deploy-modal').addEventListener('click', e => { if (e.target === e.currentTarget) closeDeployModal(); });

      document.getElementById('deploy-modal-copy-btn').addEventListener('click', function() {
        const val = document.getElementById('deploy-modal-url').value;
        if (navigator.clipboard) navigator.clipboard.writeText(val);
        else { const t = document.createElement('input'); t.value = val; document.body.appendChild(t); t.select(); document.execCommand('copy'); document.body.removeChild(t); }
        const orig = this.textContent; this.textContent = '✓ Copié !'; setTimeout(() => { this.textContent = orig; }, 2000);
      });

      document.getElementById('deploy-modal-save-btn').addEventListener('click', async function() {
        const btn = this;
        btn.disabled = true;
        const errEl = document.getElementById('deploy-modal-error');
        errEl.style.display = 'none';

        // 1. Exécuter les suppressions en attente
        for (const id of pendingDeletes) {
          await fetch(`/api/admin/characters/${encodeURIComponent(id)}`, { method: 'DELETE' });
        }
        pendingDeletes.clear();
        // Retirer physiquement les items supprimés du DOM
        document.querySelectorAll('.char-item.pending-delete').forEach(el => el.remove());

        // 2. Collecter les IDs cochés
        const charIds = [...document.querySelectorAll('.char-include-check:checked')].map(cb => cb.value);
        if (!charIds.length) {
          errEl.textContent = 'Sélectionne au moins un personnage.';
          errEl.style.display = 'block';
          btn.disabled = false; return;
        }

        try {
          let res, data;
          if (deployEditingId) {
            res = await fetch('/api/deployments/' + deployEditingId, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ character_ids: charIds }),
            });
            data = await res.json();
            if (res.ok) { closeDeployModal(); loadDeployments(); }
            else { errEl.textContent = data.error || 'Erreur.'; errEl.style.display = 'block'; btn.disabled = false; }
          } else {
            res = await fetch('/api/deploy', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ character_ids: charIds }),
            });
            data = await res.json();
            if (res.ok) { closeDeployModal(); loadDeployments(); }
            else { errEl.textContent = data.error || 'Erreur.'; errEl.style.display = 'block'; btn.disabled = false; }
          }
        } catch {
          errEl.textContent = 'Erreur réseau.';
          errEl.style.display = 'block';
          btn.disabled = false;
        }
      });

      // ── Feedback helper ────────────────────────────────────────────
      function showFeedback(id, type, msg) {
        const el = document.getElementById(id);
        if (!type) { el.style.display = 'none'; return; }
        el.className = `feedback ${type}`; el.textContent = msg; el.style.display = 'block';
      }
    </script>
  </body>
</html>
"""

result = head + new_body + new_script
path.write_text(result, encoding="utf-8")
print("Done —", len(result), "chars")
