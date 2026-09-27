# FilesOptim

[![tests](https://github.com/SKOHscripts/FilesOptim/actions/workflows/tests.yml/badge.svg)](https://github.com/SKOHscripts/FilesOptim/actions/workflows/tests.yml)
[![support](https://brianmacdonald.github.io/Ethonate/svg/eth-support-blue.svg)](https://brianmacdonald.github.io/Ethonate/address#0xEDa4b087fac5faa86c43D0ab5EfCa7C525d475C2)

**Une boîte à outils pour garder un PC Linux léger et rangé**, avec la sécurité comme priorité :

| Commande | Rôle |
|---|---|
| `filesoptim optimize` | Compresse les JPEG, PNG, GIF et PDF **sans perte** (résultat vérifié pixel par pixel ou page par page), et réencode les vidéos en HEVC/AV1 **seulement si c'est utile et visuellement sans perte** (qualité mesurée par SSIM ou VMAF). |
| `filesoptim sort` | Trie un dossier dans une arborescence complète (`Photos/2019/06`, `Music/Artiste/Album`…), renomme par date, complète les dates manquantes et ajoute des mots-clés XMP. **Prévisualisation obligatoire, et annulation possible.** |
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

**Remplacement sûr**
- L'original n'est remplacé qu'à la fin, de façon **atomique**, en conservant ses permissions, son propriétaire et ses dates. Une interruption (Ctrl+C) ne laisse jamais de fichier à moitié écrit.
- Pour les traitements avec perte (vidéo), l'original part par défaut **dans la corbeille** (`--keep-originals trash|backup|never`).
- Les fichiers ignorés d'office :
  - fichiers modifiés il y a moins de 60 s (peut-être encore en cours d'écriture) ;
  - fichiers avec plusieurs liens physiques ;
  - fichiers sans droit d'écriture ;
  - fichiers dont l'extension ne correspond pas au contenu ;
  - fichiers cachés, `.git`, `node_modules`… ;
  - les autres systèmes de fichiers montés ne sont pas parcourus.
- Si l'espace disque libre ne suffit pas pour un réencodage vidéo en toute sécurité, le fichier est ignoré.

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

Chaque commande a son aide : `filesoptim <commande> --help`.

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

---

Based on the original FilesOptim shell script, with help from Maximilian Fries's PDF script (2016) [@MokaMokiMoke](https://github.com/MokaMokiMoke). MIT license.
