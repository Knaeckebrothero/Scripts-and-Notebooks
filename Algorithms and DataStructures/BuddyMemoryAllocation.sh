#!/bin/bash
#Léon Enrique Diez Helfrich und Niklas Riel
#
#Studiengang: IBIS/EBIS
#Modul: BSRN
#Dozent: Prof. Dr. Christian Baun

#Funktion um die Nullen, falls sie nötig sind, an die ProzessNummer zu hängen um die benötigte Länge von 5 zu erreichen
function neueProzessNummer(){
	ProzessNummer=$1
	L=${#ProzessNummer}

		while [[ $L -lt 5  ]]
		do

		ProzessNummer="0${ProzessNummer}"
		((L++))

		done

	ProzessNummerString=$ProzessNummer
	
}




#Funtion zum löschen der Nullen am Anfang der Prozessnummer zum weiteren verwenden
function ProzessIDint(){

	local id=$1
	i=0
	 while [[ $i -lt 5 ]] 
		do
			extrahierung=`expr substr $id 1 1`
			if [[ $extrahierung = "0" ]]
				then
					id=${id:1}
				fi
				((i++))
			done
		IntID=$id
		return $IntID
}

#Funktion zum Wiederherstellen des benötigten Formats der einzelnen Prozessnummer
function ProzessIDString(){

	local id=$1
	IDlength=${#id}

	while [[ $IDlength -lt 5 ]]
		do 
			id="0${id}"
			((IDlength++))
		done
			StringID=$id
			return $StringID
}


#
function Zahlodernicht(){

	read prozesssize
re='^[0-9]+$'
	if ! [[ $prozesssize =~ $re ]] ; then
	   echo "$(tput smul)$(tput bold)$(tput setaf 1)Das angegebe war leider keine Zahl." >&2;
	   echo "$(tput setaf 1)Bitte versuche es erneut.$(tput sgr0)$(tput setaf 7)"
		Zahlodernicht
	fi
}

#Funktion zum herausfinden welchen Index die erste Stelle in einem Buddy besitzt
function belegtodernicht(){
	local JaOderNein=$1
	FreiOrNot=${JaOderNein:0:1}
	
}

#Funktion teilt den Buddy ab der zweiten und bis nach der sechsten Stelle, was die Prozessnummer ist
function ProzessID(){
	local Prozess=$1
	ProzessNummer=${Prozess:1:5}

}

#Funktion teilt den Buddy ab der siebten Stelle und gibt so die Buddygröße zurück
function PartitionSize(){
	local Size=$1
	BuddySize=${Size:6}

}

#Die Funktion geht das Array der Buddyliste durch und und vergleicht ob ein Buddy mit der benötigten Größe vorhanden ist und ob dieser auch frei ist.
#Wenn das der Fall ist dann wird die Stelle im Array des gefunden Buddies an die Funktion übergeben.
#Sollte dies nicht der Fall sein ändert sich dieser Wert welcher in der Funktion Prozesszuweisung einen Stop in der Funktion auslöst

function freieBuddyAnfrage(){

prozessSize=$1
Sicherung=100000
ergebnis=0


for (( i=${#ganzebuddyliste[@]} - 1; i>=0 ; i-- )); do 

		PartitionSize ${ganzebuddyliste[$i]}
		belegtodernicht ${ganzebuddyliste[$i]}

		if [[ $BuddySize -ge $prozessSize && $FreiOrNot -ne 1 ]]
			then
				if [[ $BuddySize -lt $Sicherung ]]; then

					Sicherung=$BuddySize
					ergebnis=$i
				fi

		fi
	done

	if [[ $Sicherung = 100000 ]]
		then 
		
		ergebnis=65536
	fi

	return $ergebnis

    
}
#Beginn einer der beiden Hauptfunktionen des Simulators
function Prozesszuweisung(){

	prozessSize=$1
	freieBuddyAnfrage $prozessSize
	Buddiestelle=$?


#Sicherung um die Funktion zu beenden
	 if [[ $ergebnis = 65536 ]] 
	 then 
				echo "$(tput smul)$(tput bold)$(tput setaf 1)Prozess konnte nicht zugewiesen werden.$(tput sgr0)$(tput setaf 7)"
				return 0
	fi
#Erstellung eines neuen Arrays um die neu erzeugten Buddies temopär zu Speichern
	declare -a neueBuddies


#Herausfinden der ProzessNummer und der Prozessgröße des Buddies an der Stelle die in freieBuddyAnfrage festgestellt wurde. 
	besterBuddy=${ganzebuddyliste[$Buddiestelle]}
	ProzessID $besterBuddy
	PartitionSize $besterBuddy
#Prüfung ob die doppelte Prozessgröße des Prozesses der zugewiesen werden soll größer ist als die Größe des Prozesses des Buddies der durch freieBuddyAnfgage herausgefunden wurde
	if [[ $(($prozessSize*2)) -gt $BuddySize ]]
	then 
#Wenn dies der Fall ist dann wird als einziges der Status des Buddies auf belegt geändert und er wird in der Liste der Buddies gespeichert 
#Zudem wird seine ProzessNummer im Array mit den besetzten Prozessen gespeichert
		besterBuddy=${besterBuddy/#0/1}
		ganzebuddyliste=("${ganzebuddyliste[@]:0:$Buddiestelle}" "$besterBuddy" "${ganzebuddyliste[@]:$(($Buddiestelle + 1))}")
		besetzebuddyliste=("${besetzebuddyliste[@]}" "$ProzessNummer")
		neueProzessNummer $ProzessNummer
		besetzeProzessliste=("${besetzeProzessliste[@]}" "$ProzessNummerString")		
		return 0
	fi
	
#Sollte die Prozessgröße des Prozesses der zugewiesen werden soll nicht doppelt so groß sein, dann wird hier eine neue Prozessnummer erstellt
	nPN=`expr $ProzessNummer \* 2`
#Daraufhin wird geschaut ob die doppelte Größe des Prozesses der zugewiesen werden soll kleiner oder gleich so groß ist wie die Größe des Prozesses des Buddies mit dem wir arbeiten
#Sollte dies der Fall sein, dann wird ein neuer Buddie erstellt, welcher halb so groß ist wie der Buddy mit dem wir arbeiten
#Dieser Vorgang wiederholt sich solange bis das doppelte des Prozesses der zugewiesen werden soll größer ist als der neu erstellte Buddy 
#Alle dabei erstellten Buddies kommen ins Array für die neu erstellten Buddies
	while [[ $(($prozessSize*2)) -le $BuddySize ]] 
		do
		neueProzessNummer $nPN
		BuddySize=`expr $BuddySize / 2`
		neuerBuddy="0$ProzessNummerString$BuddySize"
		neueBuddies=("${neueBuddies[@]}" "${neuerBuddy}")
		nPN=$(( ($nPN + 1) * 2 ))

		done
#Hier wird mit der gleichen Methode wie oben drüber in der Schleife ein letzter Buddy erstellt, welcher ebenfalls ins Array für die neu erstellten Buddies kommt
		nPN=`expr $nPN / 2`
		neueProzessNummer $nPN
		neuerBuddy="1$ProzessNummerString$BuddySize"
		neueBuddies=("${neueBuddies[@]}" "${neuerBuddy}")
#Hier werden das Array der neu erstellten Buddies und das davor vorhandene Array der gesamten Buddies, welche vor der Zuweisung bereits existierten, zusammengeführt
#Dabei wird der Buddy der geteilt wurde ersetzt und demnach aus dem Speicher entfernt
		ganzebuddyliste=(${ganzebuddyliste[@]:0:$Buddiestelle} ${neueBuddies[@]} ${ganzebuddyliste[@]:$(($Buddiestelle + 1))})
		besetzeProzessliste=("${besetzeProzessliste[@]}" "$ProzessNummerString")
		#echo "$besetzeProzessliste"
}


#Funktion um die belegten Prozessnummern anzuzeigen und einen auszuwählen
function ProzessNummerAuswahl(){

echo "$(tput setaf 4)Welcher Prozess soll aus dem Hauptspeicher entfernt werden?$(tput setaf 7)"
select Prozessauswahl in ${besetzeProzessliste[@]}
	do
		
		AnfangsProzess=$Prozessauswahl
		
	return $Prozessauswahl

done

}
#Funktion zum herausfinden aus welcher Prozessnummer eine andere Prozessnummer enstanden ist
function ursprungsBuddy(){
	Mo=$1
	
	ProzessIDint $Mo
	if [[ $(($IntID % 2)) == 0 ]]
		then


			Ursprung=`expr $IntID / 2 `
			ProzessIDString $Ursprung
			Quelle=$StringID

		elif [[ $(($IntID % 2)) == 1 ]]
		then
			Ursprung=`expr $IntID - 1`
			Ursprung=`expr $Ursprung / 2`
			ProzessIDString $Ursprung
			Quelle=$StringID

		fi
	


	

}


#Funktion zum herausfinden welche Prozessnummern, welche bei der Teilung eines Buddies entstanden sind, zusammengehören 
function zugehörigerBuddy(){

		Jo=$1
		
		
			ProzessIDint $Jo
		if [[ $(( $IntID % 2 )) == 0 ]]
		then

			BruderBuddy=`expr $IntID + 1`
			ProzessIDString $BruderBuddy
			Bro=$StringID


		elif [[ $(($IntID % 2)) == 1 ]]
		then

			BruderBuddy=`expr $IntID - 1`
			ProzessIDString $BruderBuddy
			Bro=$StringID
			
		fi
		

}

#Diese Funktion fügt zwei zusammengehörige Buddies wieder zusammen und kreiert einen neuen Buddie
function zusammenfuhr(){

#Diese Schleife überprüft ob die Prozessnummer der des BruderBuddys entspricht und fügt den ElternBuddy ein.
for (( a=${#ganzebuddyliste[@]}-1 ; a>=0 ; a-- ))
 do

		ProzessID ${ganzebuddyliste[$a]}
		if [[ $ProzessNummer == $Bro ]]
		then
			PartitionSize ${ganzebuddyliste[$a]}

			newBaeSize=`expr $BuddySize \* 2`
			newBae="0$Quelle$newBaeSize"

		fi
done

#Diese Schleife sucht und entfernt den ursprungsprozess.
for (( b=${#ganzebuddyliste[@]} ; b>=0 ; b-- )) 
	do
		ProzessID ${ganzebuddyliste[$b]}
		if [[ $ProzessNummer == $Prozessauswahl ]]
		then
			unset	ganzebuddyliste[$b]
			ganzebuddyliste=$ganzebuddyliste
		fi
	done
#Diese Schleife sucht und entfernt den BruderBuddy.
for (( c=${#ganzebuddyliste[@]} ; c>=0 ; c-- ))
	do
		ProzessID ${ganzebuddyliste[$c]}
		if [[ $ProzessNummer == $Bro ]]
		then
			unset	ganzebuddyliste[$c]
			
			ganzebuddyliste=("${ganzebuddyliste[@]}" "$newBae" )
		fi

	done
	
	
#Am ende ruft die Funktion sich wieder auf (Rekursion).
	ProzessID $newBae
	Prozessauswahl=$ProzessNummer
	
	
	Prozessfreigabe
}



#Beginn einer der zwei Hauptfunktionen der Simulation
function Prozessfreigabe(){
#Diese Schleife durchsucht die gesamte Buddyliste und gibt die Stelle im Array zurück der der Buddy die selbe Prozessnummer wie die Prozessauswahl besitzt
for ((j=${#ganzebuddyliste[@]}-1 ; j>=0 ; j-- ))
	do
	ProzessID ${ganzebuddyliste[$j]}
	if [[ $Prozessauswahl == $ProzessNummer ]]
	then
		Freigabestelle=$j
	fi
	done

	zugehörigerBuddy $Prozessauswahl
	ursprungsBuddy $Prozessauswahl

#Abfrage um zu Prüfen, ob der Buddy der Ausgangsbuddy (Anfangsbuddy/unaufgeteilter Speicher) ist
#Ist dies der Fall dann wird der Status des Buddys geändert und die Liste der besetzten Prozesse gelöscht
#return bricht die Funktion
if [[ $Bro == $Quelle ]] 
then
			
			FreigelegterProzess=${ganzebuddyliste[0]/#1/0}
			ganzebuddyliste[0]=$FreigelegterProzess

			unset besetzeProzessliste
	return 0
fi

#Diese Schleife geht durch die ganze Buddyliste und vergleicht die Prozessnummer mit der des Bruders.

for (( i=${#ganzebuddyliste[@]}-1 ; i>=0 ; i-- )) do

		ProzessID ${ganzebuddyliste[$i]}
	if [[ $ProzessNummer == $Bro ]]
	then
#Wenn diese übereinstimmt prüft er ob der Bruder frei ist. Wenn der Bruder nicht frei ist ändert er den Status. Wenn der Bruder doch frei ist ruft er die Funktion Zusammenfuhr auf.
		belegtodernicht ${ganzebuddyliste[$i]}
		if [[ $FreiOrNot == 1 ]]
		then
			FreigelegterProzess=${ganzebuddyliste[$Freigabestelle]/#1/0}
			ganzebuddyliste[$Freigabestelle]=$FreigelegterProzess
			
		
		elif [[ $FreiOrNot == 0 ]] 
		then
		 
		zusammenfuhr

		fi
	elif [[ $ProzessNummer != $Bro ]]
	then
		belegtodernicht ${ganzebuddyliste[$i]}
		if [[ $FreiOrNot == 1 ]]
		then
			FreigelegterProzess=${ganzebuddyliste[$Freigabestelle]/#1/0}
			ganzebuddyliste[$Freigabestelle]=$FreigelegterProzess
		fi
	fi

done
return 0

}

function testtest(){

read memory
 
#interger test

re='^[0-9]+$'
	if ! [[ $memory =~ $re ]] ; then
	   echo "$(tput smul)$(tput bold)$(tput setaf 1)Das Angegebene war leider keine Zahl." >&2;
	   echo "$(tput setaf 1)Bitte versuche es erneut.$(tput sgr0)$(tput setaf 7)"
	   testtest
	   else

#zweierpotenztest

	    if ! is_power_of_two "$memory"; then
	        echo "$(tput smul)$(tput bold)$(tput setaf 1)Die angegebene Speichergröße ist keine Zweierpotenz"
	        echo "$(tput setaf 1)Bitte versuche es erneut.$(tput sgr0)$(tput setaf 7)"
	        testtest
	    fi 
	fi
#Cap für die Speicherzuweisung Aufgrund der Prozessnummer Länge
	if [[ $memory > 32768 ]] 
 	then
		echo "$(tput smul)$(tput bold)$(tput setaf 1)Die angegebene Zweierpotenz ist leider zu groß."
		echo "Die Größte zugelassene Speichergröße beträgt 32768."
		echo "$(tput setaf 1)Bitte versuche es erneut.$(tput sgr0)$(tput setaf 7)"
 	testtest
 	fi
}

#Funktion für Zweierpotenztest

function is_power_of_two () {
    declare -i n=$memory
    (( n > 0 && (n & (n - 1)) == 0 ))
}

#Wahl der drei Befehle (Zuweisung von Prozess, Beendung von Prozess, Beendung von Prgogramm)
function auswahl(){
Options="Zuweisung Prozessstop Programmbeendigung"

echo "$(tput setaf 4)Was wollen sie tun$(tput setaf 7)"

select option in $Options
	do
		case $option in
#Wenn Zuweisung von Prozess, Abfrage der Größe des Prozesses
		Zuweisung)
			echo "$(tput setaf 4)Wie groß ist der Prozess der zugewiesen werden soll?$(tput setaf 7)"
			Zahlodernicht
			#echo "Der gewählte Prozess ist ${prozesssize}kB groß."
			Prozesszuweisung $prozesssize
			echo "$(tput setaf 4)Vorhandene Buddies:$(tput setaf 7)"
			echo "${ganzebuddyliste[@]}"
			echo "$(tput setaf 4)Prozessnummer der ausgeführten Prozesse:$(tput setaf 7)"
			echo "${besetzeProzessliste[@]}"
			auswahl;;
#Wenn Beendung von Prozess, Abfrage welcher Prozess beendet werden soll
		Prozessstop)
		safe=0
				if [[ ${#besetzeProzessliste[@]} -gt 0 ]] 
				then
					ProzessNummerAuswahl
					safe=1
				fi
				if [[  $safe == 1 ]]
				then
				Prozessfreigabe $besetzeProzessliste
				
				for (( m=0 ; m<=${#besetzeProzessliste[@]}+1 ; m++ ))
				do
						

					if [[ $AnfangsProzess = ${besetzeProzessliste[$m]} ]]
					then
						
					unset besetzeProzessliste[$m]
        				
					besetzeProzessliste=$besetzeProzessliste
					echo "$(tput setaf 4)Liste der beseetzten Prozesse:$(tput setaf 7)"
					echo "${besetzeProzessliste[@]}"
					fi

				done
				fi
					echo "$(tput setaf 4)Vorhandene Buddies:$(tput setaf 7)"
					echo "${ganzebuddyliste[@]}"
				
				auswahl;;
#Wenn Beendung von Programm, exit programm
		Programmbeendigung)
			echo "$(tput setaf 4)Tippe [Y/y] um die Smulation zu beenden.$(tput setaf 7)"
			echo "$(tput setaf 4)Tippe etwas anders um mit der Simulation fortzufahren.$(tput setaf 7)"
					
					
						read x
				if [[ "$x" = "Y" || "$x" = "y" ]] 
					then 
						punkt="."
						for (( i=0 ; i<10 ; i++)) do
			
						echo "$(tput setaf 4)Simulation wird beendet${punkt}"
						sleep 0.1
						extrapunkt="."
						punkt="${punkt}${extrapunkt}"
						done
						sleep 1
						clear
				else
			
					auswahl
				fi
						exit
			
		esac
	done
}

#Anfang der Main Funktion
#Abfrage der Speichergröße, Prüfung der Eingabe, Deklaration und Ausgabe von Variablen und das aufrufen der auswahl Funktion.
echo "$(tput setaf 4)Hallo Benutzer."
echo "$(tput setaf 4)Willkommen beim Buddyspeicherverfahrenssimulator."
echo "$(tput setaf 4)Geben Sie die Hauptspeichergröße an. Sie muss eine Zweierpotenz nicht größer als 32768 sein.$(tput setaf 7)"

testtest

PS3="$(tput setaf 4)Bitte wählen Sie aus: $(tput sgr0)"

echo "$(tput setaf 4)Die gewählte Hauptspeichergröße beträgt ${memory}kB.$(tput setaf 7)"

memory="000001$memory"

declare -a ganzebuddyliste=($memory)

declare -a besetzeProzessliste

echo "$memory"

auswahl

