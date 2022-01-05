# linux-maintenance

[![support](
https://brianmacdonald.github.io/Ethonate/svg/eth-support-blue.svg)](
https://brianmacdonald.github.io/Ethonate/address#0xEDa4b087fac5faa86c43D0ab5EfCa7C525d475C2)

<p>Un script shell qui permet de faire une maintenance complète du système Linux (sous Ubuntu).
Les paquets utiles seront installés automatiquement s'ils ne le sont pas.</p>

<p>La partie nettoyage de la corbeille permet de supprimer (ou non) les fichiers présents dans la corbeille depuis plus de 1 semaine. Mais vous pouvez changer cette valeur avec la macro correspondante. Explications ici : <a href="https://linuxhandbook.com/date-command/" title="commande date">https://linuxhandbook.com/date-command/</a>

Pour lancer le script, ne pas oublier d'autoriser l'exécution : <br/>`chmod +x ./maintenance.sh`

Puis se placer dans le dossier et exécuter le script : <br/>`./maintenance.sh`

Et voilà, après tout se fait tout seul :
<b><ol>
    <li>Mise à jour</li>
    <li>Autoremove/clean</li>
    <li>"Localepurge" des fichiers inutiles en fonction de votre langue</li>
    <li>Purge des paquets supprimés</li>
    <li>Purge des kernels inutiles et mis en "manuels"</li>
    <li>Mise à jour des snaps</li>
    <li>Résolution des dépendances non présentes</li>
    <li>Nettoyage de la corbeille</li>
</ol>
</b>

---

<p>A shell script that allows to do a complete maintenance of the Linux system (under Ubuntu). Useful packages will be installed automatically if they are not.</p>

<p>The "trash cleanup" part allows to remove (or not) files that have been in the trash for more than 1 week. But you can change this value with the corresponding macro. Explanations here: <a href="https://linuxhandbook.com/date-command/" title="commande date">https://linuxhandbook.com/date-command/</a>

To launch the script, don't forget to allow the execution : <br/> chmod +x ./maintenance.sh

Then go into the folder and execute the script : <br/> ./maintenance.sh

And voilà, everything is done by itself:
<b><ol>
    <li>Updating</li>
    <li>Autoremove/clean</li>
    <li>"Localepurge" junk files according to your language</li>
    <li>Purging deleted packages</li>
    <li>Purge useless kernels and put them in "manuals".</li>
    <li>Snapshot update</li>
    <li>Resolution of dependencies not present</li>
    <li>Cleaning the basket</li>
</ol>
</b>

``` bash
#!/bin/bash

rouge='\e[1;31m'
vert='\e[1;33m'
bleu='\e[1;34m'
violet='\e[1;35m'
vert='\e[1;32m'
neutre='\e[0;m'

if [ "$UID" -eq "0" ]
then
    zenity --warning --height 80 --width 400 --title "EREUR" --text "Merci de lancez le script sans sudo : \n<b>./maintenance.sh</b>\nVous devrez entrer le mot de passe root par la suite."
    exit
fi

which notify-send > /dev/null
if [ $? = 1 ]
then
	sudo apt install -y libnotify-bin
fi

which zenity > /dev/null
if [ $? = 1 ]
then
	sudo apt install -y zenity
fi

zenity --question --no-wrap --height 40 --width 300 --title  "Maintenance d'Ubuntu" --text "Lancer la maintenance complète ?"

if [ $? == 0 ]
then
    echo ""
    echo -e -n "$vert [1/7]$rouge MISE A JOUR DES PAQUETS "
    for i in `seq 32 $COLUMNS`;
        do echo -n "."
    done
    echo -e " $neutre"
    notify-send -i system-software-update "Maintenance d'Ubuntu" "Mises à jour"
    sudo dpkg --configure -a
    sudo apt update
    sudo apt full-upgrade -y
    echo " "

    echo -e -n "$vert [2/7]$rouge MISE A JOUR DES SNAPS "
    for i in `seq 30 $COLUMNS`;
        do echo -n "."
    done
    echo -e " $neutre"
    # notify-send -i system-software-update "Maintenance d'Ubuntu" "Mise à jour des snaps"
    snap list --all
    sudo snap refresh
    echo " "

    echo -e -n "$vert [3/7]$rouge AUTO-REMOVE "
        for i in `seq 20 $COLUMNS`;
        do echo -n "."
    done
    echo -e " $neutre"
    # notify-send -i system-software-update "Maintenance d'Ubuntu" "Auto-remove"
    sudo apt autoremove --purge -y
    echo " "

    echo -e -n "$vert [4/7]$rouge CLEAN "
        for i in `seq 14 $COLUMNS`;
        do echo -n "."
    done
    echo -e " $neutre"
    # notify-send -i system-software-update "Maintenance d'Ubuntu" "Clean"
    sudo apt autoclean
    echo " "

    echo -e -n "$vert [5/7]$rouge PURGE "
        for i in `seq 14 $COLUMNS`;
        do echo -n "."
    done
    echo -e " $neutre"
    # notify-send -i system-software-update "Maintenance d'Ubuntu" "Purge"

    sudo apt purge $(COLUMNS=200 dpkg -l | grep "^rc" | tr -s ' ' | cut -d ' ' -f 2) -y
    echo " "
    echo -e -n "$vert [6/7]$rouge RESOLUTION DES DEPENDANCES "
        for i in `seq 35 $COLUMNS`;
        do echo -n "."
    done
    echo -e " $neutre"
    # notify-send -i system-software-update "Maintenance d'Ubuntu" "Réparation des dépendances"
    sudo apt install -fy
    echo ""
    TRASH=$(date --date="3 week ago" "+%d %B")

    echo -e -n "$vert [7/7]$rouge FICHIERS ANTERIEURS AU $TRASH "
        for i in `seq 40 $COLUMNS`;
        do echo -n "."
    done
    echo -e " $neutre"
    # notify-send -i system-software-update "Maintenance d'Ubuntu" "Suppression des fichiers de la corbeille antérieurs au $TRASH"
    echo " "

    which trash-cli > /dev/null
    if [ $? = 1 ]
    then
        sudo apt install -y trash-cli
    fi

    for i in `seq 0 21`;
        do trash-list | grep $(date --date="$i day ago" "+%Y-%m-%d")
    done
    echo " "
    echo " "
    for i in `seq 21 60`;
        do trash-list | grep $(date --date="$i day ago" "+%Y-%m-%d")
    done

    zenity --question --height 40 --width 300 --title "Maintenance d'Ubuntu" --text "Voulez-vous supprimer les fichiers de la corbeille antérieurs au <b>$TRASH</b> ?"
    if [ $? == 0 ]
	then
	    trash-empty 21
	fi
    notify-send -i dialog-ok "Maintenance d'Ubuntu" "Terminée avec succès"

else
    notify-send -i dialog-close "Maintenance d'Ubuntu" "Annulé"
    exit
fi
```
