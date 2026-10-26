"""Génère les workflows n8n de sourcing de profils (JSON importables).

Usage : python3 scripts/build_sourcing_workflows.py
Les fichiers sont écrits dans workflows/sourcing/.
"""
import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "workflows" / "sourcing"
NS = uuid.UUID("6f1c2b1e-3c4d-4e5f-8a9b-0c1d2e3f4a5b")


def nid(name):
    # Identifiant stable : régénérer le fichier ne change pas les ids.
    return str(uuid.uuid5(NS, name))


def node(name, type_, version, pos, params, **extra):
    n = {
        "parameters": params,
        "id": nid(name),
        "name": name,
        "type": type_,
        "typeVersion": version,
        "position": pos,
    }
    n.update(extra)
    return n


def code(name, pos, js, **extra):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js.strip() + "\n"}, **extra)


def connect(*pairs):
    """pairs : (source, cible) ou (source, cible, index_sortie)."""
    conns = {}
    for p in pairs:
        src, dst = p[0], p[1]
        out_idx = p[2] if len(p) > 2 else 0
        outs = conns.setdefault(src, {"main": []})["main"]
        while len(outs) <= out_idx:
            outs.append([])
        outs[out_idx].append({"node": dst, "type": "main", "index": 0})
    return conns


CLAUDE_URL = "https://api.anthropic.com/v1/messages"


def claude_http(name, pos):
    return node(
        name,
        "n8n-nodes-base.httpRequest",
        4.2,
        pos,
        {
            "method": "POST",
            "url": CLAUDE_URL,
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "anthropicApi",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "anthropic-beta", "value": "server-side-fallback-2026-07-01"}
                ]
            },
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.llmBody) }}",
            "options": {"timeout": 180000},
        },
        retryOnFail=True,
        maxTries=3,
        waitBetweenTries=5000,
    )


# Bout de code partagé : lire la réponse JSON de Claude.
READ_CLAUDE = r"""
function readClaude(resp, label) {
  if (!resp || !Array.isArray(resp.content)) {
    throw new Error(label + ' : réponse Claude inattendue : ' + JSON.stringify(resp).slice(0, 500));
  }
  if (resp.stop_reason === 'refusal') {
    throw new Error(label + ' : Claude a refusé la demande (' + JSON.stringify(resp.stop_details) + ')');
  }
  if (resp.stop_reason === 'max_tokens') {
    throw new Error(label + ' : réponse coupée (max_tokens). Réduis la taille des lots.');
  }
  const text = resp.content.filter(b => b.type === 'text').map(b => b.text).join('');
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new Error(label + ' : JSON illisible : ' + text.slice(0, 500));
  }
}
"""

# =====================================================================
# Workflow principal : formulaire -> requêtes -> sources -> scoring
# =====================================================================

FORM_FIELDS = [
    {"fieldLabel": "Intitulé du poste", "placeholder": "Ex : Data Engineer senior", "requiredField": True},
    {
        "fieldLabel": "Description du profil idéal",
        "fieldType": "textarea",
        "placeholder": "Contexte, missions, ce qui ferait la différence, ce que tu ne veux surtout pas...",
        "requiredField": True,
    },
    {"fieldLabel": "Compétences indispensables", "placeholder": "Séparées par des virgules : Python, Airflow, GCP"},
    {"fieldLabel": "Compétences appréciées", "placeholder": "Séparées par des virgules"},
    {"fieldLabel": "Localisation", "placeholder": "Ex : Paris, Lyon, La Réunion, remote France"},
    {"fieldLabel": "Années d'expérience minimum", "fieldType": "number", "placeholder": "Ex : 5"},
    {"fieldLabel": "Secteurs ou entreprises cibles", "placeholder": "Ex : fintech, Qonto, Alan, cabinets Big Four"},
    {"fieldLabel": "À exclure (entreprises, mots-clés)", "placeholder": "Ex : stagiaire, alternance, ESN"},
    {
        "fieldLabel": "Langue des profils",
        "fieldType": "dropdown",
        "fieldOptions": {"values": [{"option": "Français"}, {"option": "Anglais"}, {"option": "Peu importe"}]},
        "requiredField": True,
    },
    {
        "fieldLabel": "Plateformes",
        "fieldType": "dropdown",
        "fieldOptions": {
            "values": [
                {"option": "LinkedIn"},
                {"option": "Malt"},
                {"option": "GitHub"},
                {"option": "Autre site (X-ray)"},
            ]
        },
        "multiselect": True,
        "requiredField": True,
    },
    {"fieldLabel": "Autre site à cibler (domaine)", "placeholder": "Ex : behance.net ou codeur.com/-"},
    {
        "fieldLabel": "Nombre max de profils à analyser",
        "fieldType": "dropdown",
        "fieldOptions": {"values": [{"option": "20"}, {"option": "50"}, {"option": "100"}]},
        "requiredField": True,
    },
    {"fieldLabel": "Email pour recevoir les résultats", "fieldType": "email", "requiredField": True},
]

form = node(
    "📝 Formulaire de recherche",
    "n8n-nodes-base.formTrigger",
    2.2,
    [0, 300],
    {
        "authentication": "basicAuth",
        "formTitle": "Recherche de profils",
        "formDescription": (
            "Décris le profil recherché. La recherche tourne en arrière-plan "
            "(2 à 6 minutes) et les résultats notés arrivent par email et dans le Google Sheet."
        ),
        "formFields": {"values": FORM_FIELDS},
        "responseMode": "onReceived",
        "options": {
            "appendAttribution": False,
            "path": "sourcing-profils",
            "respondWithOptions": {
                "values": {
                    "respondWith": "text",
                    "formSubmittedText": "Recherche lancée. Tu recevras les profils par email d'ici quelques minutes.",
                }
            },
        },
    },
    webhookId=nid("form-webhook"),
)

config = node(
    "⚙️ Config",
    "n8n-nodes-base.set",
    3.4,
    [220, 300],
    {
        "assignments": {
            "assignments": [
                {"id": nid("c1"), "name": "SUBWF_XRAY_ID", "value": "A_REMPLIR", "type": "string"},
                {"id": nid("c2"), "name": "SUBWF_GITHUB_ID", "value": "A_REMPLIR", "type": "string"},
                {"id": nid("c3"), "name": "GSHEET_ID", "value": "1XED45FG8yd6Bkn7AcGq0FBshIU8rS-QANRqumEQK6ck", "type": "string"},
                {"id": nid("c4"), "name": "GSHEET_TAB", "value": "Profils", "type": "string"},
                {"id": nid("c5"), "name": "CLAUDE_MODEL", "value": "claude-opus-5-5", "type": "string"},
                {"id": nid("c6"), "name": "MIN_SCORE_EMAIL", "value": 60, "type": "number"},
                {"id": nid("c7"), "name": "SCORING_BATCH_SIZE", "value": 15, "type": "number"},
            ]
        },
        "includeOtherFields": True,
        "options": {},
    },
)

NORMALIZE_JS = r"""
// Transforme les réponses du formulaire en critères propres
// et prépare l'appel à Claude qui écrit les requêtes de recherche.
const f = $input.first().json;

const list = s => String(s || '').split(/[,;\n]/).map(x => x.trim()).filter(Boolean);

let picked = f['Plateformes'];
if (typeof picked === 'string') {
  try { picked = JSON.parse(picked); } catch (e) { picked = [picked]; }
}
const MAP = { 'LinkedIn': 'linkedin', 'Malt': 'malt', 'GitHub': 'github', 'Autre site (X-ray)': 'custom' };
let platforms = (picked || []).map(p => MAP[p]).filter(Boolean);

const customDomain = String(f['Autre site à cibler (domaine)'] || '')
  .trim().replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/+$/, '');
if (platforms.includes('custom') && !customDomain) {
  platforms = platforms.filter(p => p !== 'custom');
}
if (!platforms.length) {
  throw new Error('Aucune plateforme exploitable. Si tu coches "Autre site", renseigne le domaine.');
}

for (const k of ['SUBWF_XRAY_ID', 'SUBWF_GITHUB_ID', 'GSHEET_ID']) {
  if (!f[k] || f[k] === 'A_REMPLIR') {
    throw new Error('Config incomplète : renseigne ' + k + ' dans le nœud "⚙️ Config".');
  }
}

const criteria = {
  poste: String(f['Intitulé du poste'] || '').trim(),
  description: String(f['Description du profil idéal'] || '').trim(),
  competences_indispensables: list(f['Compétences indispensables']),
  competences_appreciees: list(f['Compétences appréciées']),
  localisation: String(f['Localisation'] || '').trim(),
  experience_min_annees: Number(f["Années d'expérience minimum"]) || null,
  cibles: list(f['Secteurs ou entreprises cibles']),
  exclusions: list(f['À exclure (entreprises, mots-clés)']),
  langue: f['Langue des profils'] || 'Peu importe',
};

const maxProfiles = parseInt(f['Nombre max de profils à analyser'], 10) || 50;
const searchId = 'SRC-' + $now.toFormat('yyyyLLdd-HHmmss');

const PLATFORM_HELP = {
  linkedin: "LinkedIn via Google (l'opérateur site:linkedin.com/in est ajouté automatiquement, ne l'écris pas).",
  malt: "Malt via Google (site:malt.fr/profile est ajouté automatiquement, ne l'écris pas).",
  custom: 'Le site ' + customDomain + ' via Google (site:' + customDomain + ' est ajouté automatiquement, ne l\'écris pas).',
  github: "L'API de recherche d'utilisateurs GitHub. Syntaxe GitHub uniquement : location:, language:, followers:>N, repos:>N, type:user. Pas d'opérateurs Google. La bio n'est pas cherchable : mise sur la localisation et les langages.",
};

const system = [
  "Tu es un sourceur senior. Tu transformes un besoin de recrutement en requêtes de recherche précises.",
  "Pour les plateformes passant par Google (X-ray) :",
  "- utilise les guillemets pour les intitulés exacts, OR en majuscules pour les variantes, le signe moins pour exclure ;",
  "- reste sous 25 mots par requête, Google ignore la suite ;",
  "- varie les angles entre requêtes : synonymes d'intitulé, compétence rare, localisation proche, intitulé en anglais ;",
  "- une requête trop stricte ne renvoie rien : combine au plus 2 compétences obligatoires par requête.",
  "Écris entre 2 et 4 requêtes par plateforme demandée, aucune pour les autres.",
  "google_country est le code pays ISO à 2 lettres en minuscules le plus pertinent pour la localisation (fr par défaut).",
  "scoring_guide résume en 5 à 8 lignes ce qui distingue un excellent profil d'un profil moyen pour ce besoin, il servira à noter les candidats.",
].join('\n');

const user = [
  'Plateformes à couvrir :',
  ...platforms.map(p => '- ' + p + ' : ' + PLATFORM_HELP[p]),
  '',
  'Besoin :',
  JSON.stringify(criteria, null, 2),
].join('\n');

const llmBody = {
  model: f.CLAUDE_MODEL,
  max_tokens: 16000,
  fallbacks: 'default',
  output_config: {
    effort: 'medium',
    format: {
      type: 'json_schema',
      schema: {
        type: 'object',
        additionalProperties: false,
        required: ['queries', 'google_country', 'scoring_guide'],
        properties: {
          queries: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              required: ['platform', 'query', 'angle'],
              properties: {
                platform: { type: 'string', enum: ['linkedin', 'malt', 'github', 'custom'] },
                query: { type: 'string' },
                angle: { type: 'string', description: "Ce que cette requête cherche à capter, en une phrase." },
              },
            },
          },
          google_country: { type: 'string' },
          scoring_guide: { type: 'string' },
        },
      },
    },
  },
  system,
  messages: [{ role: 'user', content: user }],
};

return [{
  json: {
    searchId,
    submittedAt: $now.toISO(),
    email: f['Email pour recevoir les résultats'],
    platforms,
    customDomain,
    maxProfiles,
    criteria,
    config: {
      SUBWF_XRAY_ID: f.SUBWF_XRAY_ID,
      SUBWF_GITHUB_ID: f.SUBWF_GITHUB_ID,
      GSHEET_ID: f.GSHEET_ID,
      GSHEET_TAB: f.GSHEET_TAB,
      CLAUDE_MODEL: f.CLAUDE_MODEL,
      MIN_SCORE_EMAIL: Number(f.MIN_SCORE_EMAIL) || 60,
      SCORING_BATCH_SIZE: Number(f.SCORING_BATCH_SIZE) || 15,
    },
    llmBody,
  },
}];
"""

TASKS_JS = READ_CLAUDE + r"""
// Une tâche = une requête à exécuter par le bon sous-workflow.
const ctx = $('🧹 Normaliser les critères').first().json;
const plan = readClaude($input.first().json, 'Génération des requêtes');

const queries = (plan.queries || []).filter(q => ctx.platforms.includes(q.platform) && q.query && q.query.trim());
if (!queries.length) throw new Error('Claude n\'a produit aucune requête exploitable.');

const SITE = {
  linkedin: 'site:linkedin.com/in',
  malt: 'site:malt.fr/profile',
  custom: 'site:' + ctx.customDomain,
};

const country = /^[a-z]{2}$/.test(plan.google_country || '') ? plan.google_country : 'fr';
const hl = ctx.criteria.langue === 'Anglais' ? 'en' : 'fr';

const xrayCount = queries.filter(q => q.platform !== 'github').length;
const githubCount = queries.length - xrayCount;
// Google renvoie 10 résultats par page : on ajuste le nombre de pages au volume demandé.
const pagesPerQuery = xrayCount ? Math.min(3, Math.max(1, Math.ceil(ctx.maxProfiles / (xrayCount * 8)))) : 0;
const perGithubQuery = githubCount ? Math.min(30, Math.max(10, Math.ceil(ctx.maxProfiles / githubCount))) : 0;

return queries.map(q => {
  const isGithub = q.platform === 'github';
  return {
    json: {
      searchId: ctx.searchId,
      platform: q.platform,
      angle: q.angle,
      rawQuery: q.query.trim(),
      query: isGithub ? q.query.trim() : SITE[q.platform] + ' ' + q.query.trim(),
      subWorkflowId: isGithub ? ctx.config.SUBWF_GITHUB_ID : ctx.config.SUBWF_XRAY_ID,
      pages: pagesPerQuery,
      perPage: perGithubQuery,
      gl: country,
      hl,
      customDomain: ctx.customDomain,
      scoringGuide: plan.scoring_guide,
    },
  };
});
"""

DEDUPE_JS = r"""
// Fusionne les résultats de toutes les sources et supprime les doublons.
const ctx = $('🧹 Normaliser les critères').first().json;

function normUrl(u) {
  try {
    const url = new URL(String(u).trim());
    let host = url.hostname.toLowerCase();
    if (/(^|\.)linkedin\.com$/.test(host)) host = 'www.linkedin.com';
    const path = decodeURIComponent(url.pathname).replace(/\/+$/, '').toLowerCase();
    return 'https://' + host + path;
  } catch (e) {
    return null;
  }
}

const byUrl = new Map();
for (const it of $input.all()) {
  const c = it.json;
  if (!c || !c.profileUrl) continue;
  const key = normUrl(c.profileUrl);
  if (!key) continue;
  const prev = byUrl.get(key);
  if (!prev) {
    byUrl.set(key, { ...c, profileUrl: key, queries: [c.query].filter(Boolean) });
  } else {
    if (c.query && !prev.queries.includes(c.query)) prev.queries.push(c.query);
    // Un profil remonté par plusieurs requêtes est souvent plus pertinent.
    if ((c.snippet || '').length > (prev.snippet || '').length) prev.snippet = c.snippet;
  }
}

const all = [...byUrl.values()]
  .sort((a, b) => b.queries.length - a.queries.length)
  .slice(0, ctx.maxProfiles);

if (!all.length) return [{ json: { noResults: true } }];

return all.map((c, i) => ({ json: { ...c, candidateId: 'C' + (i + 1), hits: c.queries.length } }));
"""

BATCH_JS = r"""
// Regroupe les profils par lots pour limiter le nombre d'appels à Claude.
const ctx = $('🧹 Normaliser les critères').first().json;
const scoringGuide = $('🗂️ Préparer les tâches').first().json.scoringGuide;
const size = ctx.config.SCORING_BATCH_SIZE;
const candidates = $input.all().map(i => i.json);

const system = [
  "Tu évalues des profils trouvés en ligne pour un besoin de recrutement précis.",
  "Tu ne vois qu'un extrait public (titre, résumé, quelques infos), pas le profil complet.",
  "Règles :",
  "- note de 0 à 100 l'adéquation avec le besoin, selon le guide de notation fourni ;",
  "- n'invente rien : si une info manque, écris \"non visible\" et baisse la confiance, pas la note par défaut ;",
  "- un profil hors localisation, hors métier ou correspondant à une exclusion prend moins de 30 ;",
  "- justification : 2 phrases factuelles qui citent ce que tu as vu dans l'extrait ;",
  "- accroche : 2 phrases d'approche personnalisées, tutoiement interdit, sans flatterie, qui citent un élément précis du profil ;",
  "- renvoie une évaluation par profil, avec le même id.",
].join('\n');

const batches = [];
for (let i = 0; i < candidates.length; i += size) {
  const chunk = candidates.slice(i, i + size).map(c => ({
    id: c.candidateId,
    plateforme: c.platform,
    url: c.profileUrl,
    nom_visible: c.name || '',
    titre_visible: c.headline || '',
    entreprise_visible: c.company || '',
    localisation_visible: c.location || '',
    extrait: c.snippet || '',
    infos_complementaires: c.extra || null,
    nb_requetes_ayant_trouve_ce_profil: c.hits,
  }));

  const user = [
    'Besoin :',
    JSON.stringify(ctx.criteria, null, 2),
    '',
    'Guide de notation :',
    scoringGuide,
    '',
    'Profils à évaluer :',
    JSON.stringify(chunk, null, 2),
  ].join('\n');

  batches.push({
    json: {
      batchIndex: batches.length,
      ids: chunk.map(c => c.id),
      llmBody: {
        model: ctx.config.CLAUDE_MODEL,
        max_tokens: 16000,
        fallbacks: 'default',
        output_config: {
          effort: 'medium',
          format: {
            type: 'json_schema',
            schema: {
              type: 'object',
              additionalProperties: false,
              required: ['evaluations'],
              properties: {
                evaluations: {
                  type: 'array',
                  items: {
                    type: 'object',
                    additionalProperties: false,
                    required: ['id', 'score', 'confiance', 'nom', 'titre_actuel', 'entreprise_actuelle',
                      'localisation', 'seniorite_estimee', 'points_forts', 'points_vigilance',
                      'justification', 'accroche'],
                    properties: {
                      id: { type: 'string' },
                      score: { type: 'integer', description: 'De 0 à 100.' },
                      confiance: { type: 'string', enum: ['faible', 'moyenne', 'forte'] },
                      nom: { type: 'string' },
                      titre_actuel: { type: 'string' },
                      entreprise_actuelle: { type: 'string' },
                      localisation: { type: 'string' },
                      seniorite_estimee: { type: 'string' },
                      points_forts: { type: 'string' },
                      points_vigilance: { type: 'string' },
                      justification: { type: 'string' },
                      accroche: { type: 'string' },
                    },
                  },
                },
              },
            },
          },
        },
        system,
        messages: [{ role: 'user', content: user }],
      },
    },
  });
}
return batches;
"""

MERGE_JS = READ_CLAUDE + r"""
// Rattache les notes de Claude aux profils et prépare les lignes du Sheet.
const ctx = $('🧹 Normaliser les critères').first().json;
const byId = new Map($('🧬 Dédoublonner').all().map(i => [i.json.candidateId, i.json]));

const rows = [];
const failed = [];
$input.all().forEach((it, idx) => {
  let parsed;
  try {
    parsed = readClaude(it.json, 'Scoring lot ' + idx);
  } catch (e) {
    failed.push(e.message);
    return;
  }
  for (const ev of parsed.evaluations || []) {
    const c = byId.get(ev.id);
    if (!c) continue;
    rows.push({
      recherche_id: ctx.searchId,
      date: ctx.submittedAt,
      poste_recherche: ctx.criteria.poste,
      score: Math.max(0, Math.min(100, Number(ev.score) || 0)),
      confiance: ev.confiance,
      nom: ev.nom || c.name || '',
      titre_actuel: ev.titre_actuel || c.headline || '',
      entreprise_actuelle: ev.entreprise_actuelle || c.company || '',
      localisation: ev.localisation || c.location || '',
      seniorite_estimee: ev.seniorite_estimee,
      plateforme: c.platform,
      url_profil: c.profileUrl,
      points_forts: ev.points_forts,
      points_vigilance: ev.points_vigilance,
      justification: ev.justification,
      accroche: ev.accroche,
      requetes: (c.queries || []).join(' || '),
      statut: 'À contacter',
      date_suppression_prevue: $now.plus({ months: 24 }).toISODate(),
    });
  }
});

if (!rows.length) {
  throw new Error('Aucun profil noté. Erreurs : ' + failed.join(' | '));
}

rows.sort((a, b) => b.score - a.score);
return rows.map(r => ({ json: r }));
"""

EMAIL_JS = READ_CLAUDE + r"""
// Construit l'email récapitulatif à partir des profils notés.
const ctx = $('🧹 Normaliser les critères').first().json;
const all = $('📊 Fusionner et trier').all().map(i => i.json);
// Les lots que Claude n'a pas pu noter sont signalés dans l'email.
const errors = [];
$('🤖 Claude · Noter les profils').all().forEach((it, idx) => {
  try { readClaude(it.json, 'Lot ' + (idx + 1)); } catch (e) { errors.push(e.message); }
});
const min = ctx.config.MIN_SCORE_EMAIL;

let shown = all.filter(r => r.score >= min);
const belowThreshold = shown.length === 0;
if (belowThreshold) shown = all.slice(0, 10);

const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const color = s => (s >= 75 ? '#1a7f37' : s >= 55 ? '#9a6700' : '#cf222e');

const rowsHtml = shown.map(r => `
  <tr>
    <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;color:${color(r.score)}">${r.score}</td>
    <td style="padding:8px;border-bottom:1px solid #eee">
      <a href="${esc(r.url_profil)}">${esc(r.nom || 'Profil')}</a><br>
      <span style="color:#555">${esc(r.titre_actuel)}${r.entreprise_actuelle ? ' · ' + esc(r.entreprise_actuelle) : ''}</span><br>
      <span style="color:#888;font-size:12px">${esc(r.plateforme)} · ${esc(r.localisation)} · confiance ${esc(r.confiance)}</span>
    </td>
    <td style="padding:8px;border-bottom:1px solid #eee;font-size:13px">
      <b>+</b> ${esc(r.points_forts)}<br><b>!</b> ${esc(r.points_vigilance)}<br>
      <i style="color:#555">${esc(r.accroche)}</i>
    </td>
  </tr>`).join('');

const sheetUrl = 'https://docs.google.com/spreadsheets/d/' + ctx.config.GSHEET_ID;
const html = `
<div style="font-family:Arial,sans-serif;max-width:900px">
  <h2 style="margin-bottom:4px">${esc(ctx.criteria.poste)}</h2>
  <p style="color:#555;margin-top:0">${all.length} profils analysés · ${all.filter(r => r.score >= min).length} au-dessus de ${min}/100 · réf. ${ctx.searchId}</p>
  ${belowThreshold ? `<p style="background:#fff8c5;padding:8px">Aucun profil n'atteint ${min}/100. Voici les 10 meilleurs : les critères sont peut-être trop serrés.</p>` : ''}
  ${errors.length ? `<p style="background:#ffebe9;padding:8px">Certains lots n'ont pas pu être notés : ${esc(errors.join(' | '))}</p>` : ''}
  <table style="border-collapse:collapse;width:100%">
    <tr style="text-align:left;background:#f6f8fa"><th style="padding:8px">Score</th><th style="padding:8px">Profil</th><th style="padding:8px">Analyse</th></tr>
    ${rowsHtml}
  </table>
  <p><a href="${sheetUrl}">Tous les profils dans le Google Sheet</a></p>
  <p style="color:#888;font-size:11px">Notes établies sur des extraits publics, à vérifier sur le profil avant tout contact.
  RGPD : ces personnes doivent être informées de la collecte au plus tard au premier contact, et dans un délai d'un mois maximum.
  Purge prévue des lignes à la date indiquée dans le Sheet.</p>
</div>`;

return [{
  json: {
    to: ctx.email,
    subject: `[Sourcing] ${ctx.criteria.poste} : ${shown.length} profil${shown.length > 1 ? 's' : ''} retenu${shown.length > 1 ? 's' : ''}`,
    html,
  },
}];
"""

normalize = code("🧹 Normaliser les critères", [440, 300], NORMALIZE_JS)
gen_queries = claude_http("🤖 Claude · Générer les requêtes", [660, 300])
tasks = code("🗂️ Préparer les tâches", [880, 300], TASKS_JS)
run_sources = node(
    "🔎 Lancer les recherches",
    "n8n-nodes-base.executeWorkflow",
    1.2,
    [1100, 300],
    {
        "workflowId": {"__rl": True, "value": "={{ $json.subWorkflowId }}", "mode": "id"},
        "mode": "each",
        "options": {"waitForSubWorkflow": True},
    },
    alwaysOutputData=True,
    onError="continueRegularOutput",
)
dedupe = code("🧬 Dédoublonner", [1320, 300], DEDUPE_JS)
has_results = node(
    "Des profils trouvés ?",
    "n8n-nodes-base.if",
    2.2,
    [1540, 300],
    {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
            "conditions": [
                {
                    "id": nid("cond-noresults"),
                    "leftValue": "={{ $json.noResults === true }}",
                    "rightValue": "",
                    "operator": {"type": "boolean", "operation": "false", "singleValue": True},
                }
            ],
            "combinator": "and",
        },
        "options": {},
    },
)
batch = code("📦 Lots pour scoring", [1760, 200], BATCH_JS)
score = claude_http("🤖 Claude · Noter les profils", [1980, 200])
merge = code("📊 Fusionner et trier", [2200, 200], MERGE_JS)
sheet = node(
    "📄 Enregistrer dans Google Sheets",
    "n8n-nodes-base.googleSheets",
    4.5,
    [2420, 200],
    {
        "operation": "append",
        "documentId": {
            "__rl": True,
            "value": "={{ $('🧹 Normaliser les critères').first().json.config.GSHEET_ID }}",
            "mode": "id",
        },
        "sheetName": {
            "__rl": True,
            "value": "={{ $('🧹 Normaliser les critères').first().json.config.GSHEET_TAB }}",
            "mode": "name",
        },
        "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": []},
        "options": {},
    },
    onError="continueRegularOutput",
)
build_email = code("✉️ Construire l'email", [2640, 200], EMAIL_JS, executeOnce=True)
send_email = node(
    "📬 Envoyer les résultats",
    "n8n-nodes-base.gmail",
    2.1,
    [2860, 200],
    {
        "sendTo": "={{ $json.to }}",
        "subject": "={{ $json.subject }}",
        "emailType": "html",
        "message": "={{ $json.html }}",
        "options": {"appendAttribution": False},
    },
)
send_empty = node(
    "📭 Prévenir : aucun résultat",
    "n8n-nodes-base.gmail",
    2.1,
    [1760, 420],
    {
        "sendTo": "={{ $('🧹 Normaliser les critères').first().json.email }}",
        "subject": "=[Sourcing] {{ $('🧹 Normaliser les critères').first().json.criteria.poste }} : aucun profil trouvé",
        "emailType": "html",
        "message": (
            "=<p>Aucun profil trouvé pour <b>{{ $('🧹 Normaliser les critères').first().json.criteria.poste }}</b> "
            "(réf. {{ $('🧹 Normaliser les critères').first().json.searchId }}).</p>"
            "<p>Requêtes utilisées :</p><ul>{{ $('🗂️ Préparer les tâches').all().map(i => '<li>' + i.json.query + '</li>').join('') }}</ul>"
            "<p>Pistes : élargir la localisation, retirer une compétence indispensable, ou vérifier dans les exécutions n8n "
            "qu'une source n'a pas renvoyé d'erreur (clé API, quota).</p>"
        ),
        "options": {"appendAttribution": False},
    },
)

sticky = node(
    "Note",
    "n8n-nodes-base.stickyNote",
    1,
    [-40, -40],
    {
        "content": (
            "## Sourcing de profils\n"
            "1. Importer aussi les 2 sous-workflows et copier leurs IDs dans **⚙️ Config**.\n"
            "2. Renseigner l'ID du Google Sheet (onglet `Profils`).\n"
            "3. Credentials : Basic Auth (formulaire), Anthropic, Google Sheets, Gmail.\n"
            "4. Activer le workflow, puis ouvrir l'URL de production du formulaire.\n\n"
            "Détails : `workflows/sourcing/README.md`"
        ),
        "height": 240,
        "width": 420,
    },
)

main_wf = {
    "name": "SOURCING_00_Formulaire_Recherche_Profils",
    "nodes": [sticky, form, config, normalize, gen_queries, tasks, run_sources, dedupe, has_results,
              batch, score, merge, sheet, build_email, send_email, send_empty],
    "connections": connect(
        (form["name"], config["name"]),
        (config["name"], normalize["name"]),
        (normalize["name"], gen_queries["name"]),
        (gen_queries["name"], tasks["name"]),
        (tasks["name"], run_sources["name"]),
        (run_sources["name"], dedupe["name"]),
        (dedupe["name"], has_results["name"]),
        (has_results["name"], batch["name"], 0),
        (has_results["name"], send_empty["name"], 1),
        (batch["name"], score["name"]),
        (score["name"], merge["name"]),
        (merge["name"], sheet["name"]),
        (sheet["name"], build_email["name"]),
        (build_email["name"], send_email["name"]),
    ),
    "settings": {"executionOrder": "v1", "saveManualExecutions": True},
    "pinData": {},
}

# =====================================================================
# Sous-workflow 1 : recherche X-ray via Google (Serper)
# =====================================================================

XRAY_PAGES_JS = r"""
// Une requête Google par page de résultats.
const out = [];
for (const it of $input.all()) {
  const t = it.json;
  const pages = Math.max(1, Number(t.pages) || 1);
  for (let p = 1; p <= pages; p++) {
    out.push({ json: { ...t, page: p } });
  }
}
return out;
"""

XRAY_PARSE_JS = r"""
// Transforme les résultats Google en fiches profil.
const tasks = $('📄 Pages à interroger').all().map(i => i.json);

const PROFILE_URL = {
  linkedin: /linkedin\.com\/in\/[^/?#]+/i,
  malt: /malt\.[a-z.]+\/profile\/[^/?#]+/i,
};

function splitTitle(title) {
  // "Jeanne Martin - Data Engineer - Qonto | LinkedIn" -> [nom, titre, entreprise]
  const clean = String(title || '')
    .replace(/\s*[|·]\s*(LinkedIn|Malt)\s*$/i, '')
    .replace(/\s*\.\.\.\s*$/, '');
  return clean.split(/\s+[-–—]\s+/).map(s => s.trim()).filter(Boolean);
}

function guessLocation(snippet) {
  const m = String(snippet || '').match(/(?:Lieu|Location|Localisation)\s*:\s*([^·|.]+)/i);
  return m ? m[1].trim() : '';
}

const out = [];
$input.all().forEach((it, idx) => {
  const t = tasks[idx] || {};
  const organic = (it.json && it.json.organic) || [];
  for (const r of organic) {
    const link = r.link || '';
    const pattern = PROFILE_URL[t.platform];
    if (pattern && !pattern.test(link)) continue;
    if (t.platform === 'custom' && t.customDomain && !link.toLowerCase().includes(t.customDomain.split('/')[0].toLowerCase())) continue;

    const parts = splitTitle(r.title);
    out.push({
      json: {
        searchId: t.searchId,
        platform: t.platform,
        source: 'google-xray',
        profileUrl: pattern ? 'https://' + link.match(pattern)[0] : link,
        name: parts[0] || '',
        headline: parts[1] || '',
        company: parts.slice(2).join(' - '),
        location: guessLocation(r.snippet),
        snippet: [r.title, r.snippet].filter(Boolean).join(' · '),
        query: t.query,
        googlePosition: r.position,
      },
    });
  }
});
return out;
"""

xray_trigger = node(
    "▶️ Appelé par le workflow principal",
    "n8n-nodes-base.executeWorkflowTrigger",
    1.1,
    [0, 300],
    {"inputSource": "passthrough"},
)
xray_pages = code("📄 Pages à interroger", [220, 300], XRAY_PAGES_JS)
xray_http = node(
    "🔍 Serper · Recherche Google",
    "n8n-nodes-base.httpRequest",
    4.2,
    [440, 300],
    {
        "method": "POST",
        "url": "https://google.serper.dev/search",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ q: $json.query, gl: $json.gl, hl: $json.hl, num: 10, page: $json.page }) }}",
        "options": {"batching": {"batch": {"batchSize": 3, "batchInterval": 1000}}, "timeout": 30000},
    },
    retryOnFail=True,
    maxTries=3,
    waitBetweenTries=3000,
    onError="continueRegularOutput",
)
xray_parse = code("🧾 Extraire les profils", [660, 300], XRAY_PARSE_JS)

xray_wf = {
    "name": "SOURCING_01_Source_XRay_Google",
    "nodes": [xray_trigger, xray_pages, xray_http, xray_parse],
    "connections": connect(
        (xray_trigger["name"], xray_pages["name"]),
        (xray_pages["name"], xray_http["name"]),
        (xray_http["name"], xray_parse["name"]),
    ),
    "settings": {"executionOrder": "v1", "callerPolicy": "workflowsFromSameOwner"},
    "pinData": {},
}

# =====================================================================
# Sous-workflow 2 : GitHub (API officielle)
# =====================================================================

GH_LIST_JS = r"""
// Liste les comptes trouvés par la recherche GitHub.
const task = $('▶️ Appelé par le workflow principal').first().json;
const res = $input.first().json || {};
if (res.message && !res.items) {
  // Requête refusée (syntaxe ou quota) : on n'arrête pas toute la recherche pour autant.
  console.log('GitHub search error: ' + res.message);
  return [];
}
return (res.items || [])
  .filter(u => u.type === 'User')
  .slice(0, task.perPage || 20)
  .map(u => ({ json: { apiUrl: u.url, query: task.query, searchId: task.searchId } }));
"""

GH_NORMALIZE_JS = r"""
// Fiche profil homogène avec les autres sources.
const list = $('📋 Lister les comptes').all().map(i => i.json);
return $input.all()
  .map((it, idx) => ({ u: it.json || {}, t: list[idx] || {} }))
  .filter(({ u }) => u.html_url)
  .map(({ u, t }) => ({
    json: {
      searchId: t.searchId,
      platform: 'github',
      source: 'github-api',
      profileUrl: u.html_url,
      name: u.name || u.login,
      headline: u.bio || '',
      company: (u.company || '').replace(/^@/, ''),
      location: u.location || '',
      snippet: [u.bio, u.company, u.location, u.blog].filter(Boolean).join(' · '),
      extra: {
        login: u.login,
        followers: u.followers,
        public_repos: u.public_repos,
        hireable: u.hireable,
        blog: u.blog || null,
        email_public: u.email || null,
        compte_cree_le: u.created_at,
        derniere_activite: u.updated_at,
      },
      query: t.query,
    },
  }));
"""

gh_trigger = node(
    "▶️ Appelé par le workflow principal",
    "n8n-nodes-base.executeWorkflowTrigger",
    1.1,
    [0, 300],
    {"inputSource": "passthrough"},
)
gh_search = node(
    "🐙 GitHub · Rechercher des utilisateurs",
    "n8n-nodes-base.httpRequest",
    4.2,
    [220, 300],
    {
        "url": "https://api.github.com/search/users",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "githubApi",
        "sendQuery": True,
        "queryParameters": {
            "parameters": [
                {"name": "q", "value": "={{ $json.query }}"},
                {"name": "per_page", "value": "={{ $json.perPage || 20 }}"},
            ]
        },
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Accept", "value": "application/vnd.github+json"}]},
        "options": {"timeout": 30000},
    },
    retryOnFail=True,
    maxTries=2,
    waitBetweenTries=5000,
    onError="continueRegularOutput",
)
gh_list = code("📋 Lister les comptes", [440, 300], GH_LIST_JS)
gh_detail = node(
    "🐙 GitHub · Détail du profil",
    "n8n-nodes-base.httpRequest",
    4.2,
    [660, 300],
    {
        "url": "={{ $json.apiUrl }}",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "githubApi",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Accept", "value": "application/vnd.github+json"}]},
        "options": {"batching": {"batch": {"batchSize": 10, "batchInterval": 1000}}, "timeout": 30000},
    },
    retryOnFail=True,
    maxTries=2,
    waitBetweenTries=3000,
    onError="continueRegularOutput",
)
gh_norm = code("🧾 Normaliser les profils", [880, 300], GH_NORMALIZE_JS)

gh_wf = {
    "name": "SOURCING_02_Source_GitHub",
    "nodes": [gh_trigger, gh_search, gh_list, gh_detail, gh_norm],
    "connections": connect(
        (gh_trigger["name"], gh_search["name"]),
        (gh_search["name"], gh_list["name"]),
        (gh_list["name"], gh_detail["name"]),
        (gh_detail["name"], gh_norm["name"]),
    ),
    "settings": {"executionOrder": "v1", "callerPolicy": "workflowsFromSameOwner"},
    "pinData": {},
}

OUT.mkdir(parents=True, exist_ok=True)
for fname, wf in [
    ("SOURCING_00_Formulaire_Recherche_Profils.json", main_wf),
    ("SOURCING_01_Source_XRay_Google.json", xray_wf),
    ("SOURCING_02_Source_GitHub.json", gh_wf),
]:
    (OUT / fname).write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("écrit", OUT / fname)
