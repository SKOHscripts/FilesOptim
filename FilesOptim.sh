 #!/bin/bash

 rouge='\e[1;31m'
 vert='\e[1;33m'
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

zenity --question --width=500 --height=100 --title  "FilesOptim" --text "This script will recursively optimise the size of .JPG and .PNG files without loss of quality, some video files before converting them to .MP4 and finally .PDF files without loss of colour quality and fixing the images at 300DPI.\n\nAre you OK with that ?"
if [ $? == 0 ]
then
  zenity --info --width=300 --height=100 --text "Please select the folder from where you want to start optimization."
  inputStr=$(zenity --file-selection --directory "${HOME}")
  cd $inputStr
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

   find . -type f \( -iname "*.jpg" -o -iname "*.jpeg" \) >> jpg_files.txt
   IFS=$'\n'       # make newlines the only separator
   set -f          # disable globbing
   for i in $(cat < "./jpg_files.txt"); do
     echo "$i"
     jpegoptim -p -t "$i"
   done
   if [ ! -s "./jpg_files.txt" ]
   then
     echo -e " $violet"
     echo "No JPG File in Directory"
     echo -e " $neutre"
   fi
  rm ./jpg_files.txt

  which optipng > /dev/null
  if [ $? = 1 ]
  then
   sudo apt install -y optipng
  fi

   find . -type f \( -iname "*.png" \) >> png_files.txt
   IFS=$'\n'       # make newlines the only separator
   set -f          # disable globbing
   for i in $(cat < "./png_files.txt"); do
     echo "$i"
     optipng -keep -preserve -verbose "$i"
   done
   if [ ! -s "./png_files.txt" ]
   then
     echo -e " $violet"
     echo "No PNG File in Directory"
     echo -e " $neutre"
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
  find . -type f -iname "*.mp4" -o -iname '*.mkv' -o -iname '*.avi' -o -iname '*.m4v' -o -iname '*.wmv'>> paths_file.txt

  which ffmpeg > /dev/null
  if [ $? = 1 ]
  then
   sudo apt install -y ffmpeg
  fi

  IFS=$'\n'       # make newlines the only separator
  set -f          # disable globbing
  for i in $(cat < "./paths_file.txt"); do
    echo "$i"
    ffmpeg -y -i "$i" -vcodec libx265 -crf 28 ${i%%c.*}.mp4 &&  mv ${i%%c.*}.mp4 "$i" || rm ${i%%c.*}.mp4
  done

  if [ ! -s "./paths_file.txt" ]
  then
    echo -e " $violet"
    echo "No VIDEO File in Directory"
    echo -e " $neutre"
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
  ## Last revised 2016-07-29

  # Usage
  # ./pdf-compress.sh [screen|ebook|prepress|default] [verbose]

  # Variables and preparation

  which gs > /dev/null
  if [ $? = 1 ]
  then
   sudo apt install -y gs
  fi

  {
  count=0
  success=0
  successlog=./success.tmp
  gain=0
  gainlog=./gain.tmp
  pdfs=$(find ./ -type f -name "*.pdf")
  total=$(echo "$pdfs" | wc -l)
  log=./log
  verbose="-dQUIET"
  mode="printer"
  echo "0" | tee $successlog $gainlog > /dev/null
  }

  # Are there any PDFs?
  if [ "$total" -gt 0 ]; then

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

  echo "$pdfs" | while read -r file
  do
    ((count++))
    echo "Processing File #$count of $total Files" | tee -a $log
    echo "Current File: $file "| tee -a $log
    gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 -dPDFSETTINGS="/$mode" -dNOPAUSE \
    -dBATCH $verbose -sOutputFile="$file_new" "$file" | tee -a $log

    sizeold=$(wc -c "$file" | cut -d' ' -f1)
    sizenew=$(wc -c "$file_new" | cut -d' ' -f1)
    difference=$((sizenew-sizeold))

    # Check if new filesize is smaller
    if [ $difference -lt 0 ]
    then
      rm "$file"
      mv "$file_new" "$file"
      printf "Compression was successfull. New File is %'.f Bytes smaller\n" \
      $((-difference)) | tee -a $log
      ((success++))
      echo $success > $successlog
      ((gain-=difference))
      echo $gain > $gainlog
    else
      rm "$file_new"
      echo "Compression was not necessary" | tee -a $log
    fi

  done

  # Print Statistics
  printf "Successfully compressed %'.f of %'.f files\n" $(cat $successlog) $total | tee -a $log
  printf "Safed a total of %'.f Bytes\n" $(cat $gainlog) | tee -a $log

  rm $successlog $gainlog

  else
    echo "No PDF File in Directory"
  fi

else
    exit
fi
