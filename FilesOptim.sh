#!/bin/bash
#########################################################################
## A shell script made to optimize JPG, PNG, videos bnd PDF files’ sizes.
## Copyright (C) Corentin Michel - All Rights Reserved
## Contact: corentin.michel@mailo.com [https://github.com/SKOHscripts]
## With help from Maximilian Fries’s code (2016) @MokaMokiMoke[https://github.com/MokaMokiMoke]
#########################################################################
clear
rouge='\e[1;31m'
jaune='\e[1;33m'
bleu='\e[1;34m'
violet='\e[1;35m'
vert='\e[1;32m'
neutre='\e[0;m'


if [ "$UID" -eq "0" ]
then
 zenity --warning --height 80 --width 400 --title "EREUR" --text "Merci de lancez le script sans sudo : \n<b>./FilesOptim.sh</b>."
 exit
fi

which zenity > /dev/null
if [ $? = 1 ]
then
 sudo apt install -y zenity
fi

zenity --question --width=500 --height=100 --title  "FilesOptim" --text "A shell script that optimizes the size of many files. The script will optimise the size of .JPG and .PNG files without loss of quality. Next, it will recursively list video files (.mp4, .mkv, .avi, .m4v, .wmv) and optimise their size if the gain is greater than a chosen value (75% by default). Finally, it will optimise .PDF files without loss of colour quality and fixing the images at 300DPI.\n\nAre you OK with that ?"
if [ $? == 0 ]
then
 zenity --info --width=300 --height=100 --text "Please select the folder from where you want to start optimization."
 inputStr=$(zenity --file-selection --directory "${HOME}")
 cd $inputStr || { zenity --error --width=300 --height=100 --text "The folder name must not contain spaces."; exit; }


 ####################################################################################
 # PICTURES OPTIMISATION
 ####################################################################################
 echo ""
 echo -e -n "$vert [1/3]$rouge PICTURES OPTIMISATION "
 for i in `seq 30 $COLUMNS`;
   do echo -n "."
 done
 echo -e " $neutre"
 sleep 3

 which jpegoptim > /dev/null
 if [ $? = 1 ]
 then
  sudo apt install -y jpegoptim
 fi

 {
 count=0
 success=0
 successlog=./success.tmp
 find . -type f \( -iname "*.jpg" -o -iname "*.jpeg" \) >> jpg_files.txt
 total=$(wc -l < "jpg_files.txt")
 TOTAL=$(($total + 0))
 log=./log
 gainlog=./gain.tmp
 gain=0
 echo "0" | tee $successlog $gainlog > /dev/null
 }


 IFS=$'\n'       # make newlines the only separator
 set -f          # disable globbing

 if [ ! -s "./jpg_files.txt" ]
 then
   echo -e " $violet"
   echo "No JPG File in Directory"
   echo -e " $neutre"

 else
   for i in $(cat < "./jpg_files.txt"); do
     ((count++))
     echo -e " $jaune"
     echo "Processing JPG #$count of $total"
     echo -e -n " $neutre"
     sizeold=$(wc -c "$i" | cut -d' ' -f1)
     jpegoptim -p -t "$i"
     sizenew=$(wc -c "$i" | cut -d' ' -f1)
     difference=$((sizenew-sizeold))
     # Check if new filesize is smaller
     if [ $difference -lt 0 ]
     then
       printf "Compression was successfull. New File is %'.f Bytes smaller\n" \
       $((-difference)) | tee -a $log
       ((success++))
       echo $success > $successlog
       ((gain-=difference))
       echo $gain > $gainlog
     else
       printf "Compression was not necessary\n" | tee -a $log
     fi
     # Print Statistics
     printf "Successfully compressed %'.f of %'.f files\n" $(cat $successlog) $TOTAL | tee -a $log
     printf "Safed a total of %'.f Bytes\n" $(cat $gainlog) | tee -a $log
   done
   zenity --notification --width=300 --height=100 --text "Successfully compressed $(cat $successlog) of $TOTAL files\nSafed a total of $(cat gain.tmp) Bytes"

 fi
 rm ./jpg_files.txt

 sleep 2
 ####################################################################################

 which optipng > /dev/null
 if [ $? = 1 ]
 then
  sudo apt install -y optipng
 fi

 {
 count=0
 find . -type f \( -iname "*.png" \) >> png_files.txt
 total=$(wc -l < "png_files.txt")
 TOTAL=$(($TOTAL + $total))
 }

 IFS=$'\n'       # make newlines the only separator
 set -f          # disable globbing

 if [ ! -s "./png_files.txt" ]
 then
   echo -e " $violet"
   echo "No PNG File in Directory"
   echo -e " $neutre"

 else
   for i in $(cat < "./png_files.txt"); do
     ((count++))
     echo -e " $jaune"
     echo "Processing PNG #$count of $total"
     echo -e -n " $neutre"
     sizeold=$(wc -c "$i" | cut -d' ' -f1)
     optipng -o5 -preserve "$i"
     sizenew=$(wc -c "$i" | cut -d' ' -f1)
     difference=$((sizenew-sizeold))
     # Check if new filesize is smaller
     if [ $difference -lt 0 ]
     then
       printf "Compression was successfull. New File is %'.f Bytes smaller\n" \
       $((-difference)) | tee -a $log
       ((success++))
       echo $success > $successlog
       ((gain-=difference))
       echo $gain > $gainlog
     else
       printf "Compression was not necessary\n" | tee -a $log
     fi
     # Print Statistics
     printf "Successfully compressed %'.f of %'.f files\n" $(cat $successlog) $TOTAL | tee -a $log
     printf "Safed a total of %'.f Bytes\n" $(cat $gainlog) | tee -a $log
   done
   zenity --notification --width=300 --height=100 --text "Successfully compressed $(cat $successlog) of $TOTAL files\nSafed a total of $(cat gain.tmp) Bytes"
 fi
 rm ./png_files.txt


 #####################################################################################
 # VIDEO OPTIMISATION
 #####################################################################################
 echo ""
 echo -e -n "$vert [2/3]$rouge VIDEO OPTIMISATION "
 for i in `seq 27 $COLUMNS`;
     do echo -n "."
 done
 echo -e " $neutre"
 sleep 3

 which ffmpeg > /dev/null
 if [ $? = 1 ]
 then
  sudo apt install -y ffmpeg
 fi

 {
 count=0
 successlog=./success.tmp
 gainlog=./gain.tmp
 find . -type f -iname "*.mp4" -o -iname '*.mkv' -o -iname '*.avi' -o -iname '*.m4v' -o -iname '*.wmv'>> paths_file.txt
 total=$(wc -l < "paths_file.txt")
 TOTAL=$(($TOTAL + $total))
 log=./log
 # echo "0" | tee $successlog > /dev/null
 limit=75 #If video is compressed for less than 75%, the compression will be cancelled
 }

 IFS=$'\n'       # make newlines the only separator
 set -f          # disable globbing

 if [ ! -s "paths_file.txt" ]
 then
    echo -e " $violet"
    echo "No VIDEO File in Directory"
    echo -e " $neutre"

 else
    for i in $(cat < "./paths_file.txt"); do
      ((count++))
      echo -e " $jaune"
      echo "Processing vidéo file #$count of $total"
      echo "Current File: $i "
      echo -e -n " $neutre"
      extension="${i##*.}"
      new="$i.$extension"
      ffmpeg -y -i "$i" -vcodec libx265 -crf 28 "$new" || rm "$new"

      sizeold=$(wc -c "$i" | cut -d' ' -f1)
      sizenew=$(wc -c "$i.$extension" | cut -d' ' -f1)
      difference=$((sizenew-sizeold))
      perc=$((-difference*100/sizeold))

      echo ""

      # Check if new filesize is smaller
      if [ $perc -ge $limit ]
      then
        mv "$new" "$i"
        printf "Compression was successfull. New File is %'.f Bytes smaller\n" \
        $((-difference)) | tee -a $log
        ((success++))
        echo $success > $successlog
        ((gain-=difference))
        echo $gain > $gainlog
      else
        rm "$new"
        printf "Compression was not necessary\n" | tee -a $log
      fi
      # Print Statistics
      printf "Successfully compressed %'.f of %'.f files\n" $(cat $successlog) $TOTAL | tee -a $log
      printf "Safed a total of %'.f Bytes\n" $(cat $gainlog) | tee -a $log
    done
    zenity --notification --width=300 --height=100 --text "Successfully compressed $(cat $successlog) of $TOTAL files\nSafed a total of $(cat gain.tmp) Bytes"
 fi

 rm ./paths_file.txt


 #####################################################################################
 # PDF OPTIMISATION
 #####################################################################################
 echo ""
 echo -e -n "$vert [3/3]$rouge PDF OPTIMISATION "
 for i in `seq 25 $COLUMNS`;
     do echo -n "."
 done
 echo -e " $neutre"
 sleep 3
 ## Script to compress PDF Files using ghostscript incl. subdirs
 ## Copyright (C) 2016 Maximilian Fries - All Rights Reserved
 ## Contact: maxfries@t-online.de
 ## Last revised 2022-01-05 by Corentin Michel (https://github.com/SKOHscripts)

 # Variables and preparation

 which gs > /dev/null
 if [ $? = 1 ]
 then
  sudo apt install -y gs
 fi

 {
 count=0
 successlog=./success.tmp
 gainlog=./gain.tmp
 find . -type f -name "*.pdf" >> pdf_files.txt
 total=$(wc -l < "pdf_files.txt")
 TOTAL=$(($TOTAL + $total))
 log=./log
 verbose="-dQUIET"
 mode="printer"
 # echo "0" | tee $successlog > /dev/null
 }

 #Parameter Handling & Logging
 {
   echo "-- Debugging for Log START --"
   echo "Number of Parameters: $#"
   echo "Parameters are: $*"
   echo "-- Debugging for Log END   --"
 } >> $log



 # Only compression-mode set
 if [ $# -eq 1 ]; then
   mode="$1"
 fi

 # Also Verbose Level Set
 if [ $# -eq 2 ]; then
   mode="$1"
   verbose=""
 fi

 IFS=$'\n'       # make newlines the only separator
 set -f          # disable globbing

 if [ ! -s "pdf_files.txt" ]
 then
   echo -e " $violet"
   echo "No PDF File in Directory"
   echo -e " $neutre"

 else
   for i in $(cat < "pdf_files.txt"); do
     ((count++))
     echo -e " $jaune"
     echo "Processing PDF #$count of $total" | tee -a $log
     echo "Current File: $i "| tee -a $log
     echo -e -n " $neutre"
     gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 -dPDFSETTINGS="/$mode" -dNOPAUSE \ -dBATCH $verbose -sOutputFile="$i-new" "$i" | tee -a $log

     sizeold=$(wc -c "$i" | cut -d' ' -f1)
     sizenew=$(wc -c "$i-new" | cut -d' ' -f1)
     difference=$((sizenew-sizeold))

       # Check if new filesize is smaller
       if [ $difference -lt 0 ]
       then
         rm "$i"
         mv "$i-new" "$i"
         printf "Compression was successfull. New File is %'.f Bytes smaller\n" \
         $((-difference)) | tee -a $log
         ((success++))
         echo $success > $successlog
         ((gain-=difference))
         echo $gain > $gainlog
       else
         rm "$i-new"
         printf "Compression was not necessary\n" | tee -a $log
       fi
       # Print Statistics
       printf "Successfully compressed %'.f of %'.f files\n" $(cat $successlog) $TOTAL | tee -a $log
       printf "Safed a total of %'.f Bytes\n" $(cat $gainlog) | tee -a $log
   done
   zenity --info --width=300 --height=100 --text "Successfully compressed $(cat $successlog) of $TOTAL files\nSafed a total of $(cat gain.tmp) Bytes"
 fi
 rm $successlog $gainlog $log ./pdf_files.txt

else
   exit
fi
