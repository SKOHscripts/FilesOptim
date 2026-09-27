"""Command line help in English and French (chosen from the system language).

``FILESOPTIM_LANG`` (``fr``/``en``) overrides ``LC_ALL``, ``LC_MESSAGES`` and ``LANG``.
Texts used as argparse option help are %-formatted by argparse: a literal percent sign must
be written ``%%`` there. Descriptions and examples are printed as they are.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

LANGUAGES = ("en", "fr")


def language(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    for variable in ("FILESOPTIM_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(variable, "").strip().lower()
        if value and value not in ("c", "posix"):
            return "fr" if value.startswith("fr") else "en"
    return "en"


EN: dict[str, str] = {
    # -- argparse's own labels -----------------------------------------------------------------
    "argparse.usage": "usage: ",
    "argparse.positionals": "positional arguments",
    "argparse.options": "options",
    "argparse.help": "show this help message and exit",
    "argparse.version": "show the version number and exit",
    # -- main ---------------------------------------------------------------------------------
    "prog.description": """\
FilesOptim keeps a Linux computer lean and tidy.

Every command first shows what it would do (an exact estimate or a preview) and asks
before changing anything. Nothing is ever lost: files are replaced atomically, moves never
overwrite, a sort can be undone, and Ctrl+C stops everything cleanly.""",
    "prog.epilog": """\
typical workflow for a photo library:
  1. filesoptim doctor                          check the tools (jpegoptim, ffmpeg, exiftool...)
  2. filesoptim sort ~/Photos -n                preview how files would be sorted
     filesoptim sort ~/Photos                   sort them (asks before applying)
  3. filesoptim tag ~/Photos --tags family      add keywords / missing dates, nothing moves
  4. filesoptim optimize ~/Photos --estimate-only
     filesoptim optimize ~/Photos               lossless optimisation, after an exact estimate
  5. filesoptim dupes ~/Photos                  find duplicates
  6. filesoptim clean -n                        what cleaning caches would free

good to know:
  -n / --dry-run   every command that changes something can only show its plan
  -y / --yes       apply without the question (the preview is still printed)
  filesoptim undo  reverts the last sort or tag run
  filesoptim COMMAND --help   detailed help and many examples for each command

help language: follows LANG; force it with FILESOPTIM_LANG=fr or FILESOPTIM_LANG=en.""",
    "common.verbose": "show details (every file, every external command run)",
    "common.quiet": "only print warnings and errors",
    "common.no_color": "disable colours",
    "common.config": "use this configuration file instead of ~/.config/filesoptim/config.toml",
    "walk.include_hidden": "also visit hidden files and folders (names starting with '.')",
    "walk.exclude": "skip files/folders whose name matches this glob pattern, e.g. '*.tmp' "
                    "or 'Backups' (repeatable)",
    "walk.cross": "also enter other mounted disks found inside the folders",
    "confirm.yes": "apply without asking (the estimate / preview is still shown)",
    "confirm.dry_run": "only show what would be done, change nothing",
    # -- optimize -----------------------------------------------------------------------------
    "optimize.summary": "optimise images, PDF and videos without visible loss (exact estimate "
                        "first)",
    "optimize.description": """\
Reduce the size of JPEG, PNG, GIF, PDF and video files without visible loss.

1. An exact estimate is always computed first: images and PDF are really optimised in a
   temporary folder (so the announced size is exact); videos are measured on samples spread
   over each video (size and quality). Then you are asked to confirm.
2. Images: lossless optimisation, checked pixel by pixel. PDF: lossless restructuring with
   qpdf (checked with qpdf --check and page count). Videos: HEVC or AV1 re-encoding kept
   only if the gain is large enough AND the measured quality (SSIM/VMAF) stays high.
3. Nothing is compressed twice: processed files are remembered, re-encoded videos carry a
   marker, efficient codecs / HDR / signed or encrypted PDF are never touched.
4. Originals are replaced atomically (permissions and dates kept). For lossy video
   re-encodes the original goes to the trash by default.""",
    "optimize.epilog": """\
examples:
  filesoptim optimize ~/Photos --estimate-only
      exact estimate only: how much would be saved, file type by file type
  filesoptim optimize ~/Photos
      estimate, question, then optimise every image/PDF/video of ~/Photos
  filesoptim optimize ~/Photos -t jpeg,png -y
      only JPEG and PNG, without the question (unattended)
  filesoptim optimize ~/Photos -t jpeg --jpeg-progressive
      progressive JPEG: usually a few more % saved, still lossless
  filesoptim optimize ~/Documents -t pdf
      lossless PDF optimisation (qpdf)
  filesoptim optimize ~/Documents -t pdf --pdf-mode ebook
      LOSSY PDF (Ghostscript, images downsampled): much smaller, originals to the trash
  filesoptim optimize ~/Videos -t video --estimate-only -v
      per-video estimate (predicted size and SSIM of every video)
  filesoptim optimize ~/Videos -t video --codec av1 --min-ssim 0.985
      AV1 with a stricter quality floor
  filesoptim optimize ~/Videos -t video --crf 20 --preset slow
      higher quality (lower CRF) and better compression (slower preset)
  filesoptim optimize ~/Videos -t video --keep-originals backup --backup-dir /mnt/backup
      keep a copy of every original in /mnt/backup instead of the trash
  filesoptim optimize ~/Photos --exclude 'RAW' --exclude '*.tmp'
      skip the RAW folder and temporary files
  filesoptim optimize ~/Photos --force
      examine again files already processed (normally skipped)

notes:
  lossless images/PDF only need jpegoptim (or jpegtran), optipng (or oxipng), gifsicle, qpdf;
  videos need ffmpeg; run `filesoptim doctor` to check. Interrupting (Ctrl+C) is always
  safe. Default thresholds are in `filesoptim config` (section [optimize] and [video]).""",
    "optimize.paths": "folders or files to optimise",
    "optimize.types": "only these types, comma separated: jpeg, png, gif, pdf, video "
                      "(default: all)",
    "optimize.estimate_only": "stop after the estimate, change nothing (same as --dry-run)",
    "optimize.force": "ignore the memory of already processed files and examine them again",
    "optimize.jobs": "parallel workers for images/PDF (default: number of CPUs)",
    "optimize.min_saving": "minimum gain in %% for images/PDF (default 1): smaller gains are "
                           "ignored",
    "optimize.keep_originals": "what to do with originals: auto (trash for lossy re-encodes "
                               "only, default), never, trash, backup",
    "optimize.backup_dir": "folder receiving the originals with --keep-originals backup",
    "optimize.progressive": "convert JPEG to progressive (lossless, often smaller)",
    "optimize.codec": "video codec: hevc (default, very compatible) or av1 (smaller, slower)",
    "optimize.crf": "video quality: lower = better and bigger (default 22 for hevc, 30 for av1)",
    "optimize.preset": "encoder speed/size trade-off, e.g. fast, medium, slow (hevc) or 4-8 "
                       "(av1)",
    "optimize.metric": "video quality measure: ssim (default) or vmaf (needs ffmpeg with "
                       "libvmaf)",
    "optimize.min_ssim": "minimum SSIM, 0 to 1 (default 0.98): below, the re-encode is refused",
    "optimize.min_vmaf": "minimum VMAF, 0 to 100 (default 95)",
    "optimize.video_min_saving": "minimum video gain in %% (default 20)",
    "optimize.pdf_mode": "lossless (default, qpdf) or a LOSSY Ghostscript mode: printer, "
                         "ebook, screen, prepress, default",
    "optimize.min_age": "skip files modified less than this many seconds ago (default 60)",
    # -- sort ---------------------------------------------------------------------------------
    "sort.summary": "sort files into folders (Year/Month...), with preview and undo",
    "sort.description": """\
Sort files into a folder structure computed from their date (photos, videos), their tags
(music) or their type (documents, archives...).

The date comes from the metadata (EXIF, QuickTime), then from the file name
(IMG_20190612_101530, 2019-06-12 10.15.30, IMG-20190612-WA0001...), and as a last resort
from the modification time (--no-mtime forbids that).

A preview is always shown first. Nothing is ever overwritten: a name conflict gets a _1
suffix, an identical file is reported as a duplicate. `filesoptim undo` reverts the sort.

Destination templates (per category: image, video, audio, document, archive, other) accept
{year} {month} {day} {date:%Y-%m-%d} {camera} {artist} {album} {title} {track} {genre}
{ext} {EXT} {folder} {stem}. Defaults: Photos/{year}/{month}, Videos/{year}/{month},
Music/{artist}/{album}, Documents/{ext}, Archives, Other.""",
    "sort.epilog": """\
examples:
  filesoptim sort ~/Downloads -n
      preview only: where every file of ~/Downloads would go
  filesoptim sort ~/Downloads
      sort in place (Photos/2019/06/..., Documents/pdf/...), after a question
  filesoptim sort ~/Downloads/Phone --dest ~/Photos --only image,video
      move only photos and videos from the phone dump into ~/Photos
  filesoptim sort /media/sdcard/DCIM --dest ~/Photos --copy
      COPY from a memory card (the card is left untouched)
  filesoptim sort ~/Photos --leave-sorted "{year}/*" \\
        --template "image={year}/{year}-{month}" --template "video={year}/{year}-{month}" \\
        --only image,video --no-mtime --prune-empty -n
      library partly sorted by hand in Year/Event: never touch those folders, sort only the
      loose photos into 2021/2021-03/..., photos without a reliable date into Undated/
  filesoptim sort ~/Photos --rename
      rename to the date: IMG_4032.JPG -> 2019-06-12_10-15-30.jpg
  filesoptim sort ~/Photos --fix-dates --tag --tags "family,holidays"
      also write missing dates and XMP keywords while sorting
  filesoptim sort ~/Music --only audio --rename
      Music/Artist/Album/03 - Title.mp3 from the music tags
  filesoptim sort ~/Photos --template "image=Pictures/{year}/{camera}"
      one-off template without editing the configuration
  filesoptim sort ~/Photos --export-plan ~/plan.json -n
      save the complete plan to review it at leisure
  filesoptim undo
      revert the last sort

notes:
  to add keywords or dates WITHOUT moving anything, use `filesoptim tag`. Permanent templates
  go in the configuration: `filesoptim config --init` then edit [sort.templates].""",
    "sort.source": "folder to sort (sub-folders included unless --flat)",
    "sort.dest": "destination root (default: the folder itself, sorting in place)",
    "sort.copy": "copy instead of moving (the source stays untouched)",
    "sort.rename": "rename files from their date (photos/videos) or music tags",
    "sort.fix_dates": "write the date found in the file name into the metadata (EXIF / "
                      "QuickTime) when it is missing",
    "sort.tag": "add XMP keywords (category, camera, event folder)",
    "sort.tags": "your own keywords, comma separated (implies --tag)",
    "sort.only": "only these categories, comma separated: image, video, audio, document, "
                 "archive, other",
    "sort.leave_sorted": "never touch files in folders matching this pattern (relative to "
                         "SOURCE, sub-folders included): * = one folder name, ** = any depth, "
                         "{year}, {month}; e.g. \"{year}/*\" (repeatable)",
    "sort.template": "destination for a category without editing the configuration, e.g. "
                     "\"image={year}/{year}-{month}\" (repeatable)",
    "sort.no_mtime": "never date a file from its modification time (undated files go to "
                     "Undated/)",
    "sort.set_mtime": "set the file modification time to the date found (useful for "
                      "galleries sorting by date)",
    "sort.prune_empty": "remove the source folders left empty after moving",
    "sort.flat": "do not look into sub-folders",
    "sort.show_all": "list every planned operation (default: the first 30)",
    "sort.export_plan": "save the complete plan as JSON in this file",
    # -- tag ----------------------------------------------------------------------------------
    "tag.summary": "add keywords / missing dates to photos and videos, without moving them",
    "tag.description": """\
Write metadata into photos and videos where they are: nothing is moved or renamed.

- XMP keywords (read by digiKam, Shotwell, darktable, Lightroom, most photo apps):
  the category (Photo/Video), the camera model, the event folder name (e.g. "Vacances
  Bretagne", generic names like DCIM or 2019-06 are ignored) and your own keywords (--tags).
  Keywords already present are not duplicated.
- --fix-dates: when a photo has no date in its metadata but one in its name
  (IMG_20190612_101530.jpg), that date is written into EXIF / QuickTime.
- --set-mtime: the file modification time is aligned on the date found.

Pixels are never touched: exiftool writes a complete new file which replaces the original
atomically. A preview is shown first, and `filesoptim undo` removes what was added.
Needs exiftool (see `filesoptim doctor`).""",
    "tag.epilog": """\
examples:
  filesoptim tag ~/Photos -n
      preview: which keywords would be added to which files
  filesoptim tag ~/Photos
      automatic keywords (category, camera, event folder), after a question
  filesoptim tag ~/Photos/2019/Vacances\\ Bretagne --tags "bretagne,family"
      add your own keywords to one event
  filesoptim tag ~/Photos --fix-dates
      also fill in missing dates from the file names
  filesoptim tag ~/Photos --fix-dates --set-mtime --no-keywords
      dates only, no keyword
  filesoptim tag ~/Photos --tags "scan" --only-my-tags
      only your keywords, no automatic ones
  filesoptim tag ~/Photos --only video --tags "family"
      only the videos
  filesoptim tag ~/Photos -y -q
      unattended (e.g. from a cron job)
  filesoptim undo
      remove what the last run added""",
    "tag.source": "folder containing the photos/videos (sub-folders included unless --flat)",
    "tag.tags": "your own keywords, comma separated",
    "tag.only_my_tags": "do not add the automatic keywords (category, camera, folder)",
    "tag.no_keywords": "do not add any keyword (with --fix-dates / --set-mtime)",
    "tag.only": "only these categories: image, video (default: both)",
    # -- undo ---------------------------------------------------------------------------------
    "undo.summary": "revert a sort or a tag run, using its journal",
    "undo.description": """\
Revert a sort or a tag run: files go back to their original place, created folders are
removed, added dates and keywords are removed. Each run is recorded in a journal
(~/.local/state/filesoptim/journals) written before every step, so even an interrupted run
can be reverted. An undo can itself be interrupted and run again.""",
    "undo.epilog": """\
examples:
  filesoptim undo -n          show what would be reverted (last run)
  filesoptim undo             revert the last run, after a question
  filesoptim undo --list      list every journal
  filesoptim undo ~/.local/state/filesoptim/journals/sort-20260927-101500-123456.jsonl
                              revert a specific (older) run""",
    "undo.journal": "journal file (default: the most recent one not yet reverted)",
    "undo.list": "list the journals and exit",
    # -- clean --------------------------------------------------------------------------------
    "clean.summary": "clean caches, old trash, logs (exact space freed shown first)",
    "clean.description": """\
Free disk space by deleting files that can safely go: thumbnails, trash items deleted long
ago, cache files unused for a long time, leftovers of interrupted FilesOptim runs, and
optionally developer caches (pip, npm, yarn, go, cargo), browser caches and system caches
(downloaded packages of apt/dnf/pacman, archived systemd journal, old crash dumps).

The exact space freed is computed and shown first, target by target.""",
    "clean.epilog": """\
examples:
  filesoptim clean -n                     what would be freed, delete nothing
  filesoptim clean                        default targets, after a question
  filesoptim clean --list                 every target and whether it is on by default
  filesoptim clean --only thumbnails,trash
  filesoptim clean --only pip,npm,browsers        opt-in developer / browser caches
  sudo filesoptim clean --system --only apt,journal
                                          system package cache and archived logs (root)

notes:
  ages are configurable ([clean] in `filesoptim config`): cache files unused for 90 days,
  trash items deleted more than 30 days ago...""",
    "clean.only": "only these targets, comma separated (see --list)",
    "clean.system": "also the system targets: package caches, systemd journal, crash dumps "
                    "(needs root)",
    "clean.list": "list the available targets and exit",
    # -- dupes --------------------------------------------------------------------------------
    "dupes.summary": "find duplicate files (and trash, delete or link them)",
    "dupes.description": """\
Find files with exactly the same content (size, then fingerprints, then a byte-by-byte
comparison right before acting). Hard links are not counted as duplicates. By default only a
report is shown; --action chooses what to do with the extra copies.""",
    "dupes.epilog": """\
examples:
  filesoptim dupes ~/Photos
      report: groups of identical files and space that can be freed
  filesoptim dupes ~/Photos --action trash
      move the extra copies to the trash (recoverable), keep the oldest one
  filesoptim dupes ~/Photos ~/Downloads --prefer ~/Photos --action trash
      keep the copy that is in ~/Photos, trash the ones in ~/Downloads
  filesoptim dupes ~ --min-size 10M --keep shortest
      only files of 10 MiB or more, keep the one with the shortest path
  filesoptim dupes ~/Photos --action hardlink
      replace copies by hard links: space freed, every path keeps working
  filesoptim dupes ~/Photos --json > dupes.json
      machine-readable output""",
    "dupes.paths": "folders to search (several allowed)",
    "dupes.min_size": "ignore files smaller than this, e.g. 100k, 10M, 1G (default: 1 byte)",
    "dupes.action": "report (default), trash (recoverable), delete, hardlink, symlink",
    "dupes.keep": "which copy is kept: oldest (default), newest, shortest (path), first",
    "dupes.prefer": "always keep the copies located in this folder (repeatable)",
    "dupes.show_all": "show every group (default: the 20 biggest)",
    "dupes.json": "print the result as JSON",
    # -- bigfiles / emptydirs / brokenlinks --------------------------------------------------
    "bigfiles.summary": "list the biggest files",
    "bigfiles.description": "List the biggest files below the given folders.",
    "bigfiles.epilog": """\
examples:
  filesoptim bigfiles ~                       the 20 biggest files of your home
  filesoptim bigfiles ~ --top 50 --min-size 1G
  filesoptim bigfiles / --cross-filesystems --json""",
    "bigfiles.paths": "folders to search",
    "bigfiles.top": "number of files to show (default 20)",
    "bigfiles.min_size": "ignore files smaller than this, e.g. 500M",
    "bigfiles.json": "print the result as JSON",
    "emptydirs.summary": "find (and remove) empty folders",
    "emptydirs.description": """\
Find folders that contain nothing (or only empty folders). Your home, the standard folders
(Desktop, Documents, Pictures...) and the folders listed in [scan] protected are never
reported. Hidden files, .git folders and mount points count as content.""",
    "emptydirs.epilog": """\
examples:
  filesoptim emptydirs ~/Photos               list the empty folders
  filesoptim emptydirs ~/Photos --delete -n   what --delete would remove
  filesoptim emptydirs ~/Photos --delete      remove them, after a question""",
    "emptydirs.paths": "folders to search",
    "emptydirs.delete": "remove the empty folders found (after a question)",
    "brokenlinks.summary": "find (and delete) broken symbolic links",
    "brokenlinks.description": "Find symbolic links whose target no longer exists.",
    "brokenlinks.epilog": """\
examples:
  filesoptim brokenlinks ~                    list them with their missing target
  filesoptim brokenlinks ~ --delete           delete them, after a question""",
    "brokenlinks.paths": "folders to search",
    "brokenlinks.delete": "delete the broken links found (after a question)",
    # -- report / doctor / config -------------------------------------------------------------
    "report.summary": "disk usage report and recoverable space",
    "report.description": """\
Where does the space go? Disk usage, content by category, biggest folders and files, files
the optimiser can examine; optionally the exact optimisation estimate and the duplicates.""",
    "report.epilog": """\
examples:
  filesoptim report                   your home folder
  filesoptim report ~/Photos --top 20
  filesoptim report ~/Photos --estimate --dupes
                                      + exact optimisation gain + duplicates (can be slow)
  filesoptim report ~ --json > report.json""",
    "report.paths": "folders to analyse (default: your home folder)",
    "report.top": "number of folders/files listed (default 10)",
    "report.estimate": "add the exact optimisation estimate (slow for videos)",
    "report.dupes": "add the duplicates",
    "report.json": "print the report as JSON",
    "doctor.summary": "check the installed tools and what each feature needs",
    "doctor.description": """\
Check the external programs (jpegoptim, optipng, gifsicle, qpdf, ffmpeg, exiftool...), the
available features and where the configuration and journals are. Missing tools come with
the install command for your distribution.""",
    "doctor.epilog": "example:\n  filesoptim doctor",
    "config.summary": "show the effective configuration or create the file",
    "config.description": """\
Show the configuration in use (defaults + your file), print the file location or create a
commented configuration file with every option and its default value.""",
    "config.epilog": """\
examples:
  filesoptim config                   the effective configuration
  filesoptim config --path            where the file is
  filesoptim config --init            create ~/.config/filesoptim/config.toml (commented)
  filesoptim config --init --force    recreate it (overwrites)""",
    "config.init": "write a commented configuration file with the defaults",
    "config.force": "overwrite an existing file with --init",
    "config.path": "print the configuration file path and exit",
}

FR: dict[str, str] = {
    # -- argparse's own labels -----------------------------------------------------------------
    "argparse.usage": "utilisation : ",
    "argparse.positionals": "arguments",
    "argparse.options": "options",
    "argparse.help": "afficher cette aide et quitter",
    "argparse.version": "afficher le numéro de version et quitter",
    # -- main ---------------------------------------------------------------------------------
    "prog.description": """\
FilesOptim garde un ordinateur Linux léger et bien rangé.

Chaque commande montre d'abord ce qu'elle ferait (estimation exacte ou aperçu) et demande
confirmation avant de modifier quoi que ce soit. Aucune donnée n'est jamais perdue : les
fichiers sont remplacés de façon atomique, les déplacements n'écrasent jamais rien, un tri
s'annule, et Ctrl+C arrête tout proprement.""",
    "prog.epilog": """\
démarche type pour une photothèque :
  1. filesoptim doctor                          vérifier les outils (jpegoptim, ffmpeg, exiftool…)
  2. filesoptim sort ~/Photos -n                aperçu du tri
     filesoptim sort ~/Photos                   trier (demande confirmation)
  3. filesoptim tag ~/Photos --tags famille     mots-clés / dates manquantes, rien ne bouge
  4. filesoptim optimize ~/Photos --estimate-only
     filesoptim optimize ~/Photos               optimisation sans perte, après estimation exacte
  5. filesoptim dupes ~/Photos                  chercher les doublons
  6. filesoptim clean -n                        ce que le nettoyage des caches libérerait

à savoir :
  -n / --dry-run   toute commande qui modifie quelque chose peut seulement montrer son plan
  -y / --yes       appliquer sans poser la question (l'aperçu reste affiché)
  filesoptim undo  annule le dernier tri ou le dernier ajout de tags
  filesoptim COMMANDE --help   aide détaillée et nombreux exemples pour chaque commande

langue de l'aide : suit LANG ; forcer avec FILESOPTIM_LANG=fr ou FILESOPTIM_LANG=en.""",
    "common.verbose": "afficher le détail (chaque fichier, chaque commande externe lancée)",
    "common.quiet": "n'afficher que les avertissements et les erreurs",
    "common.no_color": "désactiver les couleurs",
    "common.config": "utiliser ce fichier de configuration au lieu de "
                     "~/.config/filesoptim/config.toml",
    "walk.include_hidden": "parcourir aussi les fichiers et dossiers cachés (nom commençant "
                           "par '.')",
    "walk.exclude": "ignorer les fichiers/dossiers dont le nom correspond à ce motif, ex. "
                    "'*.tmp' ou 'Sauvegardes' (répétable)",
    "walk.cross": "entrer aussi dans les autres disques montés à l'intérieur des dossiers",
    "confirm.yes": "appliquer sans demander (l'estimation / l'aperçu reste affiché)",
    "confirm.dry_run": "seulement montrer ce qui serait fait, ne rien modifier",
    # -- optimize -----------------------------------------------------------------------------
    "optimize.summary": "optimiser images, PDF et vidéos sans perte visible (estimation exacte "
                        "d'abord)",
    "optimize.description": """\
Réduit la taille des JPEG, PNG, GIF, PDF et vidéos sans perte visible.

1. Une estimation exacte est toujours calculée d'abord : les images et PDF sont réellement
   optimisés dans un dossier temporaire (la taille annoncée est donc exacte) ; les vidéos
   sont mesurées sur des échantillons répartis sur toute leur durée (taille et qualité).
   Puis une confirmation est demandée.
2. Images : optimisation sans perte, vérifiée pixel par pixel. PDF : restructuration sans
   perte avec qpdf (vérifiée par qpdf --check et le nombre de pages). Vidéos : réencodage
   HEVC ou AV1 gardé seulement si le gain est suffisant ET si la qualité mesurée
   (SSIM/VMAF) reste élevée.
3. Rien n'est compressé deux fois : les fichiers traités sont mémorisés, les vidéos
   réencodées portent un marqueur, les codecs efficaces / HDR / PDF signés ou chiffrés ne
   sont jamais touchés.
4. Les originaux sont remplacés de façon atomique (droits et dates conservés). Pour les
   réencodages vidéo avec perte, l'original part par défaut dans la corbeille.""",
    "optimize.epilog": """\
exemples :
  filesoptim optimize ~/Photos --estimate-only
      estimation exacte seulement : combien serait gagné, type par type
  filesoptim optimize ~/Photos
      estimation, question, puis optimisation de toutes les images/PDF/vidéos de ~/Photos
  filesoptim optimize ~/Photos -t jpeg,png -y
      seulement JPEG et PNG, sans la question (sans surveillance)
  filesoptim optimize ~/Photos -t jpeg --jpeg-progressive
      JPEG progressif : en général quelques % de plus, toujours sans perte
  filesoptim optimize ~/Documents -t pdf
      optimisation PDF sans perte (qpdf)
  filesoptim optimize ~/Documents -t pdf --pdf-mode ebook
      PDF AVEC PERTE (Ghostscript, images réduites) : bien plus petit, originaux à la corbeille
  filesoptim optimize ~/Vidéos -t video --estimate-only -v
      estimation vidéo par vidéo (taille prévue et SSIM de chacune)
  filesoptim optimize ~/Vidéos -t video --codec av1 --min-ssim 0.985
      AV1 avec un seuil de qualité plus strict
  filesoptim optimize ~/Vidéos -t video --crf 20 --preset slow
      qualité plus haute (CRF plus bas) et meilleure compression (preset plus lent)
  filesoptim optimize ~/Vidéos -t video --keep-originals backup --backup-dir /mnt/sauvegarde
      garder une copie de chaque original dans /mnt/sauvegarde au lieu de la corbeille
  filesoptim optimize ~/Photos --exclude 'RAW' --exclude '*.tmp'
      ignorer le dossier RAW et les fichiers temporaires
  filesoptim optimize ~/Photos --force
      réexaminer les fichiers déjà traités (normalement ignorés)

remarques :
  les images/PDF sans perte demandent jpegoptim (ou jpegtran), optipng (ou oxipng), gifsicle,
  qpdf ; les vidéos demandent ffmpeg ; `filesoptim doctor` vérifie tout. Interrompre (Ctrl+C)
  est toujours sans risque. Les seuils par défaut sont visibles avec `filesoptim config`
  (sections [optimize] et [video]).""",
    "optimize.paths": "dossiers ou fichiers à optimiser",
    "optimize.types": "seulement ces types, séparés par des virgules : jpeg, png, gif, pdf, "
                      "video (défaut : tous)",
    "optimize.estimate_only": "s'arrêter après l'estimation, ne rien modifier (comme "
                              "--dry-run)",
    "optimize.force": "ignorer la mémoire des fichiers déjà traités et les réexaminer",
    "optimize.jobs": "nombre de traitements en parallèle pour images/PDF (défaut : nombre de "
                     "processeurs)",
    "optimize.min_saving": "gain minimum en %% pour images/PDF (défaut 1) : les gains plus "
                           "petits sont ignorés",
    "optimize.keep_originals": "que faire des originaux : auto (corbeille pour les "
                               "réencodages avec perte seulement, défaut), never, trash, backup",
    "optimize.backup_dir": "dossier qui reçoit les originaux avec --keep-originals backup",
    "optimize.progressive": "convertir les JPEG en progressif (sans perte, souvent plus petit)",
    "optimize.codec": "codec vidéo : hevc (défaut, très compatible) ou av1 (plus petit, plus "
                      "lent)",
    "optimize.crf": "qualité vidéo : plus bas = meilleur et plus gros (défaut 22 en hevc, 30 "
                    "en av1)",
    "optimize.preset": "compromis vitesse/taille de l'encodeur, ex. fast, medium, slow (hevc) "
                       "ou 4-8 (av1)",
    "optimize.metric": "mesure de qualité vidéo : ssim (défaut) ou vmaf (ffmpeg compilé avec "
                       "libvmaf)",
    "optimize.min_ssim": "SSIM minimum, de 0 à 1 (défaut 0.98) : en dessous, le réencodage est "
                         "refusé",
    "optimize.min_vmaf": "VMAF minimum, de 0 à 100 (défaut 95)",
    "optimize.video_min_saving": "gain vidéo minimum en %% (défaut 20)",
    "optimize.pdf_mode": "lossless (défaut, qpdf) ou un mode Ghostscript AVEC PERTE : "
                         "printer, ebook, screen, prepress, default",
    "optimize.min_age": "ignorer les fichiers modifiés il y a moins de ce nombre de secondes "
                        "(défaut 60)",
    # -- sort ---------------------------------------------------------------------------------
    "sort.summary": "trier les fichiers en dossiers (Année/Mois…), avec aperçu et annulation",
    "sort.description": """\
Range les fichiers dans une arborescence calculée à partir de leur date (photos, vidéos),
de leurs tags (musique) ou de leur type (documents, archives…).

La date vient des métadonnées (EXIF, QuickTime), puis du nom du fichier
(IMG_20190612_101530, 2019-06-12 10.15.30, IMG-20190612-WA0001…), et en dernier recours de
la date de modification (interdit avec --no-mtime).

Un aperçu est toujours affiché d'abord. Rien n'est jamais écrasé : un conflit de nom reçoit
un suffixe _1, un fichier identique est signalé comme doublon. `filesoptim undo` annule le
tri.

Les modèles de destination (par catégorie : image, video, audio, document, archive, other)
acceptent {year} {month} {day} {date:%Y-%m-%d} {camera} {artist} {album} {title} {track}
{genre} {ext} {EXT} {folder} {stem}. Par défaut : Photos/{year}/{month},
Videos/{year}/{month}, Music/{artist}/{album}, Documents/{ext}, Archives, Other.""",
    "sort.epilog": """\
exemples :
  filesoptim sort ~/Téléchargements -n
      aperçu seulement : où irait chaque fichier de ~/Téléchargements
  filesoptim sort ~/Téléchargements
      trier sur place (Photos/2019/06/…, Documents/pdf/…), après une question
  filesoptim sort ~/Téléchargements/Téléphone --dest ~/Photos --only image,video
      déplacer seulement photos et vidéos du téléphone vers ~/Photos
  filesoptim sort /media/carteSD/DCIM --dest ~/Photos --copy
      COPIER depuis une carte mémoire (la carte n'est pas modifiée)
  filesoptim sort ~/Photos --leave-sorted "{year}/*" \\
        --template "image={year}/{year}-{month}" --template "video={year}/{year}-{month}" \\
        --only image,video --no-mtime --prune-empty -n
      photothèque déjà en partie rangée en Année/Événement : ne jamais toucher ces dossiers,
      trier seulement les photos en vrac dans 2021/2021-03/…, les photos sans date fiable
      dans Undated/
  filesoptim sort ~/Photos --rename
      renommer d'après la date : IMG_4032.JPG -> 2019-06-12_10-15-30.jpg
  filesoptim sort ~/Photos --fix-dates --tag --tags "famille,vacances"
      écrire aussi les dates manquantes et des mots-clés XMP pendant le tri
  filesoptim sort ~/Musique --only audio --rename
      Music/Artiste/Album/03 - Titre.mp3 d'après les tags musicaux
  filesoptim sort ~/Photos --template "image=Images/{year}/{camera}"
      modèle ponctuel sans modifier la configuration
  filesoptim sort ~/Photos --export-plan ~/plan.json -n
      enregistrer le plan complet pour le relire tranquillement
  filesoptim undo
      annuler le dernier tri

remarques :
  pour ajouter des mots-clés ou des dates SANS rien déplacer, utiliser `filesoptim tag`.
  Les modèles permanents se règlent dans la configuration : `filesoptim config --init`
  puis modifier [sort.templates].""",
    "sort.source": "dossier à trier (sous-dossiers compris, sauf avec --flat)",
    "sort.dest": "racine de destination (défaut : le dossier lui-même, tri sur place)",
    "sort.copy": "copier au lieu de déplacer (la source n'est pas modifiée)",
    "sort.rename": "renommer les fichiers d'après leur date (photos/vidéos) ou leurs tags "
                   "musicaux",
    "sort.fix_dates": "écrire dans les métadonnées (EXIF / QuickTime) la date trouvée dans le "
                      "nom quand elle manque",
    "sort.tag": "ajouter des mots-clés XMP (catégorie, appareil, dossier d'événement)",
    "sort.tags": "vos propres mots-clés, séparés par des virgules (implique --tag)",
    "sort.only": "seulement ces catégories, séparées par des virgules : image, video, audio, "
                 "document, archive, other",
    "sort.leave_sorted": "ne jamais toucher aux fichiers des dossiers correspondant à ce motif "
                         "(relatif à SOURCE, sous-dossiers compris) : * = un nom de dossier, "
                         "** = n'importe quelle profondeur, {year}, {month} ; ex. \"{year}/*\" "
                         "(répétable)",
    "sort.template": "destination d'une catégorie sans modifier la configuration, ex. "
                     "\"image={year}/{year}-{month}\" (répétable)",
    "sort.no_mtime": "ne jamais dater un fichier par sa date de modification (les fichiers "
                     "sans date vont dans Undated/)",
    "sort.set_mtime": "caler la date de modification du fichier sur la date trouvée (utile aux "
                      "galeries qui trient par date)",
    "sort.prune_empty": "supprimer les dossiers source restés vides après les déplacements",
    "sort.flat": "ne pas descendre dans les sous-dossiers",
    "sort.show_all": "lister toutes les opérations prévues (défaut : les 30 premières)",
    "sort.export_plan": "enregistrer le plan complet en JSON dans ce fichier",
    # -- tag ----------------------------------------------------------------------------------
    "tag.summary": "ajouter mots-clés / dates manquantes aux photos et vidéos, sans les "
                   "déplacer",
    "tag.description": """\
Écrit des métadonnées dans les photos et vidéos là où elles sont : rien n'est déplacé ni
renommé.

- Mots-clés XMP (lus par digiKam, Shotwell, darktable, Lightroom et la plupart des logiciels
  photo) : la catégorie (Photo/Video), le modèle d'appareil, le nom du dossier d'événement
  (ex. « Vacances Bretagne » ; les noms génériques comme DCIM ou 2019-06 sont ignorés) et
  vos propres mots-clés (--tags). Un mot-clé déjà présent n'est jamais dupliqué.
- --fix-dates : quand une photo n'a pas de date dans ses métadonnées mais en a une dans son
  nom (IMG_20190612_101530.jpg), cette date est écrite dans l'EXIF / QuickTime.
- --set-mtime : la date de modification du fichier est calée sur la date trouvée.

Les pixels ne sont jamais touchés : exiftool écrit un nouveau fichier complet qui remplace
l'original de façon atomique. Un aperçu est affiché d'abord, et `filesoptim undo` retire ce
qui a été ajouté. Nécessite exiftool (voir `filesoptim doctor`).""",
    "tag.epilog": """\
exemples :
  filesoptim tag ~/Photos -n
      aperçu : quels mots-clés seraient ajoutés à quels fichiers
  filesoptim tag ~/Photos
      mots-clés automatiques (catégorie, appareil, dossier d'événement), après une question
  filesoptim tag ~/Photos/2019/Vacances\\ Bretagne --tags "bretagne,famille"
      ajouter vos mots-clés à un événement
  filesoptim tag ~/Photos --fix-dates
      compléter aussi les dates manquantes à partir des noms de fichiers
  filesoptim tag ~/Photos --fix-dates --set-mtime --no-keywords
      seulement les dates, aucun mot-clé
  filesoptim tag ~/Photos --tags "scan" --only-my-tags
      seulement vos mots-clés, pas les automatiques
  filesoptim tag ~/Photos --only video --tags "famille"
      seulement les vidéos
  filesoptim tag ~/Photos -y -q
      sans surveillance (ex. depuis une tâche cron)
  filesoptim undo
      retirer ce que le dernier passage a ajouté""",
    "tag.source": "dossier contenant les photos/vidéos (sous-dossiers compris, sauf avec "
                  "--flat)",
    "tag.tags": "vos propres mots-clés, séparés par des virgules",
    "tag.only_my_tags": "ne pas ajouter les mots-clés automatiques (catégorie, appareil, "
                        "dossier)",
    "tag.no_keywords": "n'ajouter aucun mot-clé (avec --fix-dates / --set-mtime)",
    "tag.only": "seulement ces catégories : image, video (défaut : les deux)",
    # -- undo ---------------------------------------------------------------------------------
    "undo.summary": "annuler un tri ou un ajout de tags grâce à son journal",
    "undo.description": """\
Annule un tri ou un ajout de tags : les fichiers reviennent à leur place d'origine, les
dossiers créés sont supprimés, les dates et mots-clés ajoutés sont retirés. Chaque passage
est enregistré dans un journal (~/.local/state/filesoptim/journals) écrit avant chaque
étape : même un passage interrompu peut être annulé. Une annulation peut elle-même être
interrompue puis relancée.""",
    "undo.epilog": """\
exemples :
  filesoptim undo -n          montrer ce qui serait annulé (dernier passage)
  filesoptim undo             annuler le dernier passage, après une question
  filesoptim undo --list      lister tous les journaux
  filesoptim undo ~/.local/state/filesoptim/journals/sort-20260927-101500-123456.jsonl
                              annuler un passage précis (plus ancien)""",
    "undo.journal": "fichier journal (défaut : le plus récent pas encore annulé)",
    "undo.list": "lister les journaux et quitter",
    # -- clean --------------------------------------------------------------------------------
    "clean.summary": "nettoyer caches, vieille corbeille, journaux (place libérée affichée "
                     "d'abord)",
    "clean.description": """\
Libère de la place en supprimant ce qui peut partir sans risque : miniatures, éléments
supprimés depuis longtemps de la corbeille, fichiers de cache inutilisés depuis longtemps,
restes d'exécutions interrompues de FilesOptim, et en option les caches de développement
(pip, npm, yarn, go, cargo), des navigateurs et du système (paquets téléchargés par
apt/dnf/pacman, journal systemd archivé, vieux rapports de plantage).

La place exacte libérée est calculée et affichée d'abord, cible par cible.""",
    "clean.epilog": """\
exemples :
  filesoptim clean -n                     ce qui serait libéré, sans rien supprimer
  filesoptim clean                        cibles par défaut, après une question
  filesoptim clean --list                 toutes les cibles et si elles sont actives par défaut
  filesoptim clean --only thumbnails,trash
  filesoptim clean --only pip,npm,browsers        caches développeur / navigateurs (optionnels)
  sudo filesoptim clean --system --only apt,journal
                                          cache des paquets et journaux archivés (root)

remarques :
  les durées se règlent dans la configuration ([clean] dans `filesoptim config`) : fichiers
  de cache inutilisés depuis 90 jours, éléments supprimés il y a plus de 30 jours…""",
    "clean.only": "seulement ces cibles, séparées par des virgules (voir --list)",
    "clean.system": "aussi les cibles système : caches de paquets, journal systemd, rapports "
                    "de plantage (root)",
    "clean.list": "lister les cibles disponibles et quitter",
    # -- dupes --------------------------------------------------------------------------------
    "dupes.summary": "trouver les doublons (et les mettre à la corbeille, supprimer ou lier)",
    "dupes.description": """\
Trouve les fichiers au contenu strictement identique (taille, puis empreintes, puis
comparaison octet par octet juste avant d'agir). Les liens physiques ne comptent pas comme
doublons. Par défaut seul un rapport est affiché ; --action choisit quoi faire des copies en
trop.""",
    "dupes.epilog": """\
exemples :
  filesoptim dupes ~/Photos
      rapport : groupes de fichiers identiques et place récupérable
  filesoptim dupes ~/Photos --action trash
      copies en trop à la corbeille (récupérables), la plus ancienne est gardée
  filesoptim dupes ~/Photos ~/Téléchargements --prefer ~/Photos --action trash
      garder la copie de ~/Photos, mettre à la corbeille celles de ~/Téléchargements
  filesoptim dupes ~ --min-size 10M --keep shortest
      seulement les fichiers de 10 Mio ou plus, garder celui au chemin le plus court
  filesoptim dupes ~/Photos --action hardlink
      remplacer les copies par des liens physiques : place libérée, tous les chemins
      continuent de fonctionner
  filesoptim dupes ~/Photos --json > doublons.json
      sortie lisible par un programme""",
    "dupes.paths": "dossiers à analyser (plusieurs possibles)",
    "dupes.min_size": "ignorer les fichiers plus petits, ex. 100k, 10M, 1G (défaut : 1 octet)",
    "dupes.action": "report (défaut), trash (récupérable), delete, hardlink, symlink",
    "dupes.keep": "quelle copie garder : oldest (plus ancienne, défaut), newest, shortest "
                  "(chemin le plus court), first",
    "dupes.prefer": "toujours garder les copies situées dans ce dossier (répétable)",
    "dupes.show_all": "afficher tous les groupes (défaut : les 20 plus gros)",
    "dupes.json": "afficher le résultat en JSON",
    # -- bigfiles / emptydirs / brokenlinks --------------------------------------------------
    "bigfiles.summary": "lister les plus gros fichiers",
    "bigfiles.description": "Liste les plus gros fichiers sous les dossiers indiqués.",
    "bigfiles.epilog": """\
exemples :
  filesoptim bigfiles ~                       les 20 plus gros fichiers de votre dossier personnel
  filesoptim bigfiles ~ --top 50 --min-size 1G
  filesoptim bigfiles / --cross-filesystems --json""",
    "bigfiles.paths": "dossiers à analyser",
    "bigfiles.top": "nombre de fichiers affichés (défaut 20)",
    "bigfiles.min_size": "ignorer les fichiers plus petits, ex. 500M",
    "bigfiles.json": "afficher le résultat en JSON",
    "emptydirs.summary": "trouver (et supprimer) les dossiers vides",
    "emptydirs.description": """\
Trouve les dossiers qui ne contiennent rien (ou seulement des dossiers vides). Le dossier
personnel, les dossiers standard (Bureau, Documents, Images…) et ceux listés dans
[scan] protected ne sont jamais signalés. Fichiers cachés, dossiers .git et points de montage
comptent comme du contenu.""",
    "emptydirs.epilog": """\
exemples :
  filesoptim emptydirs ~/Photos               lister les dossiers vides
  filesoptim emptydirs ~/Photos --delete -n   ce que --delete supprimerait
  filesoptim emptydirs ~/Photos --delete      les supprimer, après une question""",
    "emptydirs.paths": "dossiers à analyser",
    "emptydirs.delete": "supprimer les dossiers vides trouvés (après une question)",
    "brokenlinks.summary": "trouver (et supprimer) les liens symboliques cassés",
    "brokenlinks.description": "Trouve les liens symboliques dont la cible n'existe plus.",
    "brokenlinks.epilog": """\
exemples :
  filesoptim brokenlinks ~                    les lister avec leur cible manquante
  filesoptim brokenlinks ~ --delete           les supprimer, après une question""",
    "brokenlinks.paths": "dossiers à analyser",
    "brokenlinks.delete": "supprimer les liens cassés trouvés (après une question)",
    # -- report / doctor / config -------------------------------------------------------------
    "report.summary": "rapport d'occupation du disque et place récupérable",
    "report.description": """\
Où part la place ? Occupation des disques, contenu par catégorie, plus gros dossiers et
fichiers, fichiers que l'optimiseur peut examiner ; en option l'estimation exacte de
l'optimisation et les doublons.""",
    "report.epilog": """\
exemples :
  filesoptim report                   votre dossier personnel
  filesoptim report ~/Photos --top 20
  filesoptim report ~/Photos --estimate --dupes
                                      + gain exact de l'optimisation + doublons (peut être long)
  filesoptim report ~ --json > rapport.json""",
    "report.paths": "dossiers à analyser (défaut : votre dossier personnel)",
    "report.top": "nombre de dossiers/fichiers listés (défaut 10)",
    "report.estimate": "ajouter l'estimation exacte de l'optimisation (long pour les vidéos)",
    "report.dupes": "ajouter les doublons",
    "report.json": "afficher le rapport en JSON",
    "doctor.summary": "vérifier les outils installés et ce que chaque fonction demande",
    "doctor.description": """\
Vérifie les programmes externes (jpegoptim, optipng, gifsicle, qpdf, ffmpeg, exiftool…), les
fonctions disponibles et l'emplacement de la configuration et des journaux. Pour les outils
manquants, la commande d'installation adaptée à votre distribution est indiquée.""",
    "doctor.epilog": "exemple :\n  filesoptim doctor",
    "config.summary": "afficher la configuration effective ou créer le fichier",
    "config.description": """\
Affiche la configuration utilisée (valeurs par défaut + votre fichier), indique où est le
fichier ou crée un fichier de configuration commenté avec chaque option et sa valeur par
défaut.""",
    "config.epilog": """\
exemples :
  filesoptim config                   la configuration effective
  filesoptim config --path            où se trouve le fichier
  filesoptim config --init            créer ~/.config/filesoptim/config.toml (commenté)
  filesoptim config --init --force    le recréer (écrase l'existant)""",
    "config.init": "écrire un fichier de configuration commenté avec les valeurs par défaut",
    "config.force": "écraser un fichier existant avec --init",
    "config.path": "afficher le chemin du fichier de configuration et quitter",
}

TEXTS = {"en": EN, "fr": FR}


class HelpText:
    """``help_text("sort.summary")`` in the chosen language (English as fallback)."""

    def __init__(self, lang: str | None = None) -> None:
        self.lang = lang if lang in TEXTS else language()

    def __call__(self, key: str) -> str:
        return TEXTS[self.lang].get(key, EN[key])
