# FilesOptim

[![tests](https://github.com/SKOHscripts/FilesOptim/actions/workflows/tests.yml/badge.svg)](https://github.com/SKOHscripts/FilesOptim/actions/workflows/tests.yml)
[![support](https://brianmacdonald.github.io/Ethonate/svg/eth-support-blue.svg)](https://brianmacdonald.github.io/Ethonate/address#0xEDa4b087fac5faa86c43D0ab5EfCa7C525d475C2)

**Une boîte à outils pour garder un PC Linux léger et rangé**, avec la sécurité comme priorité :

| Commande | Rôle |
|---|---|
| `filesoptim optimize` | Compresse les JPEG, PNG, GIF et PDF **sans perte** (résultat vérifié pixel par pixel ou page par page), et réencode les vidéos en HEVC/AV1 **seulement si c'est utile et visuellement sans perte** (qualité mesurée par SSIM ou VMAF). |
| `filesoptim sort` | Trie un dossier dans une arborescence complète (`Photos/2019/06`, `Music/Artiste/Album`…), renomme par date, complète les dates manquantes et ajoute des mots-clés XMP. **Prévisualisation obligatoire, et annulation possible.** |
| `filesoptim tag` | Ajoute des mots-clés XMP et complète les dates **sans jamais déplacer ni renommer** : pour une photothèque déjà rangée. Prévisualisation et annulation comme pour le tri. |
| `filesoptim undo` | Annule un tri : fichiers, dossiers, dates et mots-clés reviennent à l'identique. |
| `filesoptim clean` | Vide caches, miniatures, vieille corbeille, journaux archivés et paquets téléchargés, en affichant d'abord la taille exacte libérée. |
| `filesoptim dupes` | Trouve les doublons (taille, puis empreinte, puis comparaison octet par octet) et les met à la corbeille, les supprime ou les remplace par des liens. |
| `filesoptim bigfiles` / `emptydirs` / `brokenlinks` | Gros fichiers, dossiers vides, liens symboliques cassés. |
| `filesoptim report` | Rapport disque : où va la place et combien on peut récupérer (estimation exacte avec `--estimate`). |
| `filesoptim doctor` | Vérifie les outils installés et indique comment installer ceux qui manquent. |

## Les garde-fous

**Rien n'est modifié sans aperçu.** `optimize` commence *toujours* par une estimation précise, puis demande confirmation. `sort`, `clean`, `dupes`, `emptydirs`, `brokenlinks` et `undo` affichent d'abord ce qu'ils vont faire. Sans terminal interactif, rien n'est appliqué sans `--yes`, et `--dry-run` (`-n`) s'arrête toujours après l'aperçu.

**Estimation précise avant toute optimisation**
- **Images et PDF** : l'outil est réellement exécuté sur une copie de travail. La taille annoncée est donc exacte, et le résultat vérifié est réutilisé tel quel au moment d'appliquer.
- **Vidéos** : des échantillons répartis sur toute la durée sont encodés avec les réglages finaux. Leur taille est comparée aux octets exacts des paquets vidéo d'origine dans les mêmes fenêtres de temps (lus par `ffprobe`), et la qualité est mesurée sur chaque échantillon. Sur une vidéo de test, l'estimation (1 140 481 octets) s'écarte de **0,1 %** du résultat réel (1 138 817 octets). Une vidéo courte est encodée entièrement, ce qui rend l'estimation exacte.

**Pas de perte de qualité**
- **JPEG** : optimisation Huffman sans perte (`jpegoptim`, ou `jpegtran` à défaut), progressif en option. Le résultat est **décodé et comparé pixel par pixel** à l'original.
- **PNG** : `oxipng`, ou `optipng` à défaut. **GIF** : `gifsicle -O3`. Même vérification pixel par pixel, image par image pour les animations. Les PNG animés (APNG) ne sont pas touchés.
- **PDF** : restructuration sans perte avec `qpdf` (flux d'objets, recompression flate), contrôle de structure (`qpdf --check`) et du nombre de pages. Les PDF **chiffrés** ou **signés numériquement** sont laissés tels quels. Les modes Ghostscript (`printer`, `ebook`…), avec perte, sont uniquement optionnels.
- **Vidéos** : HEVC (libx265, CRF 22 par défaut) ou AV1 (SVT-AV1 ou libaom). Le résultat est **refusé** si la SSIM mesurée passe sous 0,98 (ou la VMAF sous 95), si le gain est inférieur à 20 %, ou si la durée ne correspond pas. Les pistes audio et sous-titres sont copiées sans réencodage. La date de création et les métadonnées (GPS, appareil) sont conservées, et un marqueur empêche de réencoder une deuxième fois.

**Pas de sur-compression**
- Une vidéo déjà en HEVC, AV1, VP9 ou VVC, déjà très compressée (peu de bits par pixel), HDR, ou déjà traitée par FilesOptim n'est jamais réencodée.
- Une base d'état mémorise chaque fichier traité, par taille, date de modification et réglages : il n'est pas retraité à chaque passage (`--force` pour ignorer cette mémoire).
- Seuils de gain minimum : 1 % et 2 Kio sans perte, 20 % pour la vidéo, 10 % pour les modes PDF avec perte.

**Aucune perte de données, même en cas d'arrêt brutal** (Ctrl+C, `kill -9`, fermeture du terminal, coupure de courant)
- **Remplacement** : un fichier n'est jamais réécrit sur place. La nouvelle version est écrite à côté sous un nom temporaire, forcée sur le disque (`fsync`), puis substituée à l'original en une seule opération atomique. À tout instant, l'original complet ou la nouvelle version complète existe.
- **Modification pendant le traitement** : juste avant de remplacer, FilesOptim vérifie que l'original n'a pas changé depuis l'estimation. Une retouche faite entre-temps n'est jamais écrasée.
- **Déplacements** (tri, annulation, corbeille) :
  - sur le même disque, le fichier est d'abord créé sous son nouveau nom (lien physique), puis l'ancien nom est retiré ; au pire, il existe deux noms pour les mêmes données ;
  - vers un autre disque, la copie est écrite en entier et forcée sur le disque avant que la source soit effacée ;
  - rien n'est jamais écrasé.
- **Métadonnées** : exiftool écrit un nouveau fichier complet, qui remplace l'original de la même façon atomique. Les pixels ne sont jamais touchés.
- **Journal du tri** : chaque étape est écrite sur le disque *avant* d'être exécutée. `filesoptim undo` peut donc tout annuler, même après une interruption, et on peut le relancer s'il est lui-même interrompu.
- **Corbeille** : un fichier situé sur un autre disque va dans la corbeille de ce disque (`.Trash-UID`, comme le gestionnaire de fichiers). Aucune copie n'atterrit sur la partition système.
- **Tests de crash** : les tests tuent de vrais processus (`kill -9`) en plein tri, en pleine annulation et en pleine optimisation. Ils vérifient ensuite que chaque fichier d'origine existe toujours, identique octet pour octet (ou pixel pour pixel), et qu'aucun nom définitif ne contient une copie partielle.

**Remplacement sûr**
- Les permissions, le propriétaire et les dates de l'original sont conservés.
- Pour les traitements avec perte (vidéo), l'original part par défaut **dans la corbeille** (`--keep-originals trash|backup|never`).
- Les fichiers ignorés d'office :
  - fichiers modifiés il y a moins de 60 s (peut-être encore en cours d'écriture) ;
  - fichiers avec plusieurs liens physiques ;
  - fichiers sans droit d'écriture ;
  - fichiers dont l'extension ne correspond pas au contenu ;
  - fichiers cachés, `.git`, `node_modules`… ;
  - les autres systèmes de fichiers montés ne sont pas parcourus.
- **Espace disque protégé** : FilesOptim ne descend jamais sous **2 Gio d'espace libre** (`min_free_mb`) sur les disques où il écrit. L'estimation garde au plus 1 Gio de résultats d'essai (`staging_limit_mb`), puis recalcule le reste au moment d'appliquer. Un fichier qui ne tiendrait pas est ignoré (« not enough free disk space »).
- **Ctrl+C arrête tout immédiatement** : les fichiers en attente ne démarrent pas, les outils en cours (jpegoptim, qpdf, ffmpeg…) sont stoppés, et les fichiers temporaires sont supprimés. Fermer le terminal (SIGHUP) ou `kill` (SIGTERM) fait de même.
- **Restes d'exécutions tuées** (`kill -9`, coupure de courant) : ils sont supprimés automatiquement au lancement suivant, et `filesoptim clean` les montre (cible `filesoptim`).

## Installation

```bash
pipx install git+https://github.com/SKOHscripts/FilesOptim   # ou : pip install --user .
filesoptim doctor                                            # affiche ce qu'il manque
```

Python 3.10 ou plus récent est nécessaire (Pillow et mutagen sont installés automatiquement). Les outils externes sont facultatifs : chaque fonction se désactive proprement si son outil manque, et `doctor` donne la commande d'installation adaptée à votre distribution.

| Distribution | Commande |
|---|---|
| Debian / Ubuntu | `sudo apt install jpegoptim optipng gifsicle qpdf ffmpeg libimage-exiftool-perl` |
| Fedora | `sudo dnf install jpegoptim optipng gifsicle qpdf ffmpeg-free perl-Image-ExifTool` |
| Arch | `sudo pacman -S jpegoptim oxipng gifsicle qpdf ffmpeg perl-image-exiftool` |

## Utilisation

```bash
# Optimisation : estimation exacte, confirmation, puis application
filesoptim optimize ~/Images --estimate-only        # seulement l'estimation
filesoptim optimize ~/Images ~/Vidéos               # estimation, question, application
filesoptim optimize ~/Vidéos -t video --codec av1 --min-ssim 0.985
filesoptim optimize ~/Documents -t pdf --pdf-mode ebook   # PDF avec perte (optionnel)

# Tri avec prévisualisation, puis annulation si besoin
filesoptim sort ~/Téléchargements/Photos --dest ~/Images --rename --fix-dates --tag --tags vacances -n
filesoptim sort ~/Téléchargements/Photos --dest ~/Images --rename --fix-dates --tag
filesoptim undo            # annule le dernier tri (filesoptim undo --list pour l'historique)

# Mots-clés et dates sur place, sans rien déplacer
filesoptim tag ~/Photos -n                           # aperçu des mots-clés ajoutés
filesoptim tag ~/Photos/2019 --tags famille,plage   # + vos mots-clés
filesoptim tag ~/Photos --no-keywords --fix-dates   # dates seulement

# Maintenance
filesoptim clean -n                     # taille exacte récupérable, sans rien supprimer
filesoptim clean --only pip,npm,browsers
sudo filesoptim clean --system --only apt,journal
filesoptim dupes ~/Images --action trash --keep oldest --prefer ~/Images/Albums
filesoptim bigfiles ~ --top 30
filesoptim emptydirs ~/Projets --delete
filesoptim brokenlinks ~ --delete
filesoptim report ~ --estimate --dupes          # ou --json
```

Chaque commande a une aide détaillée, avec de nombreux exemples : `filesoptim --help`, puis `filesoptim <commande> --help`.
L'aide est en français ou en anglais selon la langue du système (`LANG`, `LC_ALL`, `LC_MESSAGES`) ; `FILESOPTIM_LANG=en` ou `FILESOPTIM_LANG=fr` force une langue.

### Tri : modèles et métadonnées

La date de chaque fichier est cherchée dans cet ordre :
1. les **métadonnées** (EXIF/QuickTime via exiftool, ou Pillow/ffprobe à défaut, tags ID3/Vorbis pour la musique) ;
2. le **nom du fichier** (`IMG_20190612_101530`, `2019-06-12 10.15.30`, `Screenshot 2023-05-14 at 15.30.00`, `IMG-20230514-WA0001`, horodatages Unix…) ;
3. en dernier recours, la **date de modification**. Ces fichiers sont signalés dans l'aperçu, et `--no-mtime` interdit ce repli.

Les dossiers de destination se règlent par catégorie dans la configuration, avec ces variables :
- dates : `{year}`, `{month}`, `{day}`, `{date:%Y-%m-%d}` ;
- fichier : `{category}`, `{ext}`, `{EXT}`, `{camera}`, `{folder}`, `{stem}` ;
- musique : `{artist}`, `{album}`, `{title}`, `{track}`, `{genre}`.

```toml
[sort.templates]
image = "Photos/{year}/{month}"
video = "Vidéos/{year}/{month}"
audio = "Musique/{artist}/{album}"
document = "Documents/{EXT}"
other = ""                      # "" : laisser en place
```

**Options de métadonnées**
- `--fix-dates` écrit la date trouvée dans le nom du fichier dans l'EXIF, ou dans les dates QuickTime pour une vidéo.
- `--tag` ajoute des mots-clés XMP (`dc:subject`), lus par digiKam, Shotwell, darktable ou Lightroom :
  - la catégorie ;
  - l'appareil photo ;
  - le dossier d'événement (« Vacances Bretagne ») ;
  - vos mots-clés, passés avec `--tags`.
- Ces écritures passent par exiftool, qui ne touche jamais aux pixels. Elles sont consignées dans le journal, donc `undo` les retire.

**Autres options**
- `--rename` renomme d'après la date (`2019-06-12_10-15-30.jpg`) ou les tags musicaux (`03 - Titre.mp3`).
- `--set-mtime` aligne la date de modification du fichier sur la date trouvée.
- Les fichiers annexes (`.xmp`, `.aae`, `.thm`) suivent leur photo.
- Un doublon exact n'est jamais déplacé par-dessus un fichier existant : il est signalé. Un conflit de nom reçoit un suffixe `_1`.

### Trier un dossier déjà en partie trié

Pour une photothèque déjà rangée en partie à la main (par exemple `2019/Vacances Bretagne/`), `--leave-sorted` indique les dossiers à ne jamais toucher. Seules les photos en vrac sont triées, directement dans la structure existante :

```bash
filesoptim sort ~/Photos \
    --leave-sorted "{year}/*" \
    --template "image={year}/{year}-{month}" --template "video={year}/{year}-{month}" \
    --only image,video --no-mtime --prune-empty -n      # aperçu ; retirer -n pour appliquer
```

- `--leave-sorted "{year}/*"` protège `2019/Vacances Bretagne/…` et ses sous-dossiers. Dans un motif :
  - `*` représente un nom de dossier ;
  - `**` représente n'importe quelle profondeur ;
  - `{year}` représente une année, `{month}` un mois.
  L'option peut être répétée, ou réglée dans la configuration (`leave_sorted`).
- Une photo en vrac identique à une photo déjà rangée n'est pas déplacée : elle est signalée comme doublon (`filesoptim dupes` permet ensuite de la supprimer).
- `--template` choisit le dossier de destination sans modifier la configuration. Ici, les photos en vrac vont dans `2021/2021-03/`, à côté des dossiers d'événements.
- Avec `--no-mtime`, les photos sans aucune date fiable vont dans `Undated/`, à vérifier à la main.
- Relancer la commande ne déplace plus rien : ce qui a été rangé correspond ensuite au motif.

### Ajouter des mots-clés sans rien déplacer

`filesoptim tag` écrit les mêmes métadonnées que `sort --tag`, mais laisse chaque fichier à sa place et sous son nom. C'est la commande à utiliser une fois la photothèque rangée :

```bash
filesoptim tag ~/Photos -n                                 # aperçu : fichier, date trouvée, changements
filesoptim tag ~/Photos                                    # catégorie, appareil, dossier d'événement
filesoptim tag ~/Photos/2019/Mariage --tags mariage,famille
filesoptim tag ~/Photos --tags archive --only-my-tags      # uniquement vos mots-clés
filesoptim tag ~/Photos --fix-dates --set-mtime            # + dates manquantes et date de modification
filesoptim tag ~/Photos --no-keywords --fix-dates          # dates seulement
filesoptim undo                                            # retire tout, à l'octet près
```

- Seuls les photos et vidéos sont traités (`--only image` ou `--only video` pour restreindre) ; exiftool est requis.
- Un mot-clé déjà présent n'est pas ajouté une seconde fois, et un fichier qui n'a rien à changer n'est pas réécrit.
- L'écriture passe par un fichier temporaire, remplacé atomiquement : un Ctrl+C ou un arrêt brutal ne laisse jamais de fichier à moitié écrit.

### Configuration

```bash
filesoptim config --init      # crée ~/.config/filesoptim/config.toml, commenté
filesoptim config             # affiche la configuration effective
```

Toutes les valeurs par défaut sont documentées dans le fichier généré : seuils de qualité et de gain, codec, CRF, preset, nombre d'échantillons, âge des caches à vider, modèles de tri, dossiers protégés, etc. La base d'état et les journaux d'annulation sont dans `~/.local/state/filesoptim/`.

## Développement

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest --cov=filesoptim --cov-branch      # 100 % des lignes et des branches exigé
pytest -m integration                      # tests avec les vrais outils (ffmpeg, exiftool…)
ruff check src tests && mypy               # lint + typage strict
```

**Couverture sans les outils** : les tests unitaires simulent les outils externes et atteignent **100 % de couverture (lignes et branches) même sans aucun outil installé**.

**Tests d'intégration** : ils vérifient le comportement réel.
- JPEG, PNG et GIF identiques au pixel près après optimisation.
- PDF chiffrés refusés.
- Estimation vidéo contre résultat réel, conservation de `creation_time`.
- Tri et annulation redonnant un fichier identique à l'octet près.

**CI** : GitHub Actions exécute tout cela sur Python 3.10 à 3.13.

---

## English

**FilesOptim keeps a Linux computer lean and tidy, safely.** The original shell script became a tested Python CLI:

| Command | What it does |
|---|---|
| `optimize` | Lossless JPEG/PNG/GIF/PDF optimisation, **verified pixel by pixel or page by page**. Visually-lossless HEVC/AV1 video re-encoding, **only when worth it**. |
| `tag` | XMP keywords and missing dates, written in place: nothing is moved or renamed. Preview and undo as for `sort`. |
| `sort` / `undo` | Sorting into `Photos/{year}/{month}`-style trees, renaming, filling in missing dates, XMP keywords. Mandatory preview; everything can be undone. |
| `clean` | Caches, thumbnails, old trash, archived journal files, package caches. |
| `dupes` | Duplicates, checked byte by byte before acting. |
| `bigfiles` / `emptydirs` / `brokenlinks` | Big files, empty folders, broken symbolic links. |
| `report` | Disk report. |
| `doctor` | Checks the environment. |

**Estimate first, always.** Every optimisation starts with a precise estimate:
- **images and PDF**: real trial runs, so the estimate is exact;
- **videos**: encoded samples compared with the exact source packet sizes, with SSIM/VMAF measured on each sample (0.1 % error on a test video).

Then it asks for confirmation.

**Guards**
- Nothing is re-compressed twice: a state database and a marker tag in re-encoded videos remember what was done.
- Efficient codecs, HDR, signed and encrypted PDFs are never touched.
- Minimum gains and quality floors reject bad results.
- Originals are replaced atomically, with their metadata preserved; lossy re-encodes keep the original in the trash.

**Installation**: `pipx install git+https://github.com/SKOHscripts/FilesOptim`, then `filesoptim doctor`.

**Help**: `filesoptim --help` and `filesoptim <command> --help` explain every option with many examples. The help follows the system language (French or English); set `FILESOPTIM_LANG=en` or `fr` to force one.

---

Based on the original FilesOptim shell script, with help from Maximilian Fries's PDF script (2016) [@MokaMokiMoke](https://github.com/MokaMokiMoke). MIT license.
