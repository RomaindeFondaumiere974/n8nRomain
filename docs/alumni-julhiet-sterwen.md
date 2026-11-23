# Cadrage : listing des alumni Julhiet Sterwen sur LinkedIn

## Contexte (demande de Julie Millet, groupe Teams "Champions IA JS")

Besoin : alimenter les viviers recrutement et business pour animer les alumni.

- **Recrutement** : consultants partis chez des concurrents depuis plus de 2 ans (ou moins de 7 ans d'expérience) qui pourraient revenir.
- **Business** : consultants partis chez des clients, avec un pouvoir de décision ou une capacité de prescription.
- Problème : les bases RH sont incomplètes (il manque environ la moitié des personnes).
- Ce que fait l'IA : un listing avec la cible (recrutement / business), la qualification (pouvoir de décision, nouvelles expertises) et le grade actuel.
- Ce que ne fait pas l'IA : le recontact. Un passage manuel retire les bad leavers.
- Fréquence : one-shot pour le stock, puis tous les 6 à 12 mois. Idéalement sans limite d'ancienneté.
- Julie a demandé à Nicolas une "licence pour faire tourner l'API LinkedIn" (environ 50 €/mois) et regarde LoneScale.

## Préconisation

### Corrections de cadrage
1. Il n'existe pas d'API LinkedIn officielle pour ce besoin. La recherche de profils est réservée aux partenaires validés, et la Profile API ne renvoie que les champs de base depuis 2023. Inutile de chercher une licence.
2. Ne pas utiliser d'outil basé sur un cookie de compte perso (PhantomBuster, Waalaxy, scrapers open source) : risque de bannissement du compte d'un collaborateur, et précédent Kaspr.

### Étape 1 : stock (one-shot)
- Actor Apify `harvestapi/linkedin-profile-search`, filtre `pastCompanies`, sans cookie. Environ 4 $/1k profils en version courte, 8 à 12 $/1k en version complète (à vérifier). Moins de 50 $ au total.
- Pipeline n8n (même découpage que JOBS_Veille v2 : orchestrateur + sous-workflows) :
  1. Recherche : pastCompanies = toutes les pages entreprise historiques (Julhiet, Sterwen, Julhiet Sterwen, variantes), en excluant l'entreprise actuelle JS.
  2. Enrichissement : profil complet (parcours).
  3. Classification LLM :
     - recrutement : employeur actuel dans la liste concurrents et plus de 2 ans d'ancienneté, ou moins de 7 ans d'expérience ;
     - business : employeur actuel dans la liste clients (CRM) avec un score de pouvoir de décision déduit de l'intitulé ;
     - grade estimé et nouvelles expertises.
  4. Sortie dans Sheets ou NocoDB, avec une colonne "bad leaver" remplie par les RH.
- Angle mort : les anciens profils pointent vers les pages entreprise d'avant la fusion, ou vers du texte libre. Recenser toutes les variantes avant de lancer.

### Étape 2 : récurrent
- Relancer tous les 6 mois et comparer avec le stock (nouveaux départs, changements de poste).
- Solution durable : à l'offboarding, demander l'URL LinkedIn de la personne et son accord pour rester dans le réseau alumni.

### Entrées à demander à Julie
- La liste des concurrents.
- La liste des clients (export CRM).
- L'export RH, même incomplet : il sert de jeu de test (prendre 30 alumni connus et mesurer combien la recherche en retrouve) et de base pour le filtre bad leaver.

### RGPD
- Sanction CNIL contre Kaspr : 240 000 € (décembre 2024).
- La fiche CNIL de 2026 admet le scraping sur la base de l'intérêt légitime, avec des mesures supplémentaires.
- Ne collecter que des données professionnelles publiques, jamais d'e-mail ni de téléphone masqués.
- Prévoir une durée de conservation et l'information des personnes au premier contact (article 14).
- Faire valider par le DPO avant de lancer.

## Sources
- https://apify.com/harvestapi/linkedin-profile-search/pricing
- https://apify.com/harvestapi/linkedin-profile-search/input-schema
- https://developer.linkedin.com/legal/api-terms-of-use
- https://learn.microsoft.com/en-ca/linkedin/compliance/announcements/data-restrictions
- https://www.cnil.fr/en/data-scraping-kaspr-fined-eu240000
- https://cnil.fr/en/legal-basis-legitimate-interest-focus-sheet-measures-implement-case-data-collection-web-scraping
- https://news.linkedin.com/2025/LinkedInWinsLegalBattleToProtectMemberData

## Prochaine étape pour Claude Code
Construire le POC n8n de l'étape 1, en partant d'un test sur 30 alumni connus pour mesurer la couverture.
