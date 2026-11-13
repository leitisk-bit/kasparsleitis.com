/* Latvian / English for the pages. Latvian is the default; the flags in the top right switch.
   The pages are written in English; this file swaps the text shown on screen (text, placeholders,
   tooltips) as it appears, also for text the pages add later and for messages from the server.
   window.T('English text') gives the text in the chosen language, for text drawn on a canvas. */
(function () {
  'use strict';
  var KEY = 'ior.lang';
  var lang = 'lv';
  try { lang = localStorage.getItem(KEY) || 'lv'; } catch (e) {}
  if (lang !== 'en') lang = 'lv';

  /* exact texts (as shown, spaces at the ends ignored) */
  var D = {
    // header, library
    'Indoor O Replay': 'Telpu O Replay',
    'Telpu orientēšanās · map library': 'Telpu orientēšanās · karšu bibliotēka',
    'Admin': 'Administrators', 'Admin sign in': 'Administratora pieslēgšanās', 'Sign out': 'Atslēgties',
    'Sign in': 'Pieslēgties', 'Admin password': 'Administratora parole', 'Wrong password.': 'Nepareiza parole.',
    'Signed in as admin.': 'Pieslēdzies kā administrators.', 'App version: development': 'Lietotnes versija: izstrāde',
    'Add a map': 'Pievienot karti', 'Map PDF': 'Kartes PDF', 'Name': 'Nosaukums', 'Event / place': 'Sacensības / vieta',
    'e.g. RK 2026 · 2. posms · A': 'piem., RK 2026 · 2. posms · A',
    'e.g. RTU Datorzinātņu centrs, 24.10.2026': 'piem., RTU Datorzinātņu centrs, 24.10.2026',
    'Upload and read map': 'Augšupielādēt un nolasīt karti', 'Uploading…': 'Augšupielādē…',
    'The map is read automatically: floors, stairwells, controls in course order, one-way arrows and the four-way crossing. You check the result on the next screen. Everyone using this server can then add their runs to it.':
      'Karte tiek nolasīta automātiski: stāvi, kāpņu telpas, kontrolpunkti distances secībā, vienvirziena bultas un krustojums. Rezultātu pārbaudi nākamajā ekrānā. Pēc tam ikviens šīs vietnes lietotājs var pievienot tai savus skrējienus.',
    'Maps': 'Kartes', 'No maps yet.': 'Vēl nav nevienas kartes.', 'Other maps': 'Citas kartes',
    'map': 'karte', 'maps': 'kartes',
    'ready': 'gatava', 'check': 'pārbaudīt', 'error': 'kļūda', 'working': 'apstrādā', 'draft': 'melnraksts',
    'Only you can see this map. Publish it on the map page.': 'Šo karti redzi tikai tu. Publicē to kartes lapā.',
    'Let Claude check draft maps': 'Ļaut Claude pārbaudīt melnraksta kartes', '(one-time setup)': '(vienreizēja iestatīšana)',
    'Copy checker key': 'Kopēt pārbaudes atslēgu', 'Add secret': 'Add secret',
    'Checker key copied. Paste it into GitHub as described.': 'Pārbaudes atslēga nokopēta. Ielīmē to GitHub, kā aprakstīts.',
    'Could not copy. Select and copy it by hand.': 'Neizdevās nokopēt. Iezīmē un nokopē to ar roku.',
    'Claude checks new maps through GitHub. Draft maps are hidden from everyone but you, so GitHub needs a':
      'Claude pārbauda jaunās kartes caur GitHub. Melnraksta kartes redzi tikai tu, tāpēc GitHub vajag',
    'view-only checker key': 'tikai lasīšanas pārbaudes atslēgu',
    '. It can read draft maps but cannot change or publish anything. It changes if you change the admin password.':
      '. Ar to var lasīt melnraksta kartes, bet neko nevar mainīt vai publicēt. Tā mainās, ja maini administratora paroli.',
    'On GitHub open': 'GitHub atver', 'Name:': 'Nosaukums:', '. Secret: paste the key. Press': '. Secret: ielīmē atslēgu. Spied',
    // map page
    'All maps': 'Visas kartes', 'Analyze routes': 'Analizēt maršrutus', 'Original PDF': 'Oriģinālais PDF',
    'Runs': 'Skrējieni', 'Controls': 'Kontrolpunkti', 'Stairs': 'Kāpnes', 'One-way & crossings': 'Vienvirziena un krustojumi',
    'Dead ends': 'Strupceļi', 'Map': 'Karte', 'Folder': 'Mape',
    'by the name, e.g. RK 2026 4. posms': 'pēc nosaukuma, piem., RK 2026 4. posms',
    'Folder from the name again': 'Mape atkal pēc nosaukuma', 'Moved to folder': 'Pārvietota uz mapi',
    'Map scale 1:': 'Kartes mērogs 1:', 'Saved': 'Saglabāts', 'Saving…': 'Saglabā…',
    'Import results with splits': 'Importēt rezultātus ar starplaikiem', 'Easiest:': 'Vienkāršāk:',
    "paste the race's lof.lv link, for example": 'ielīmē sacensību lof.lv saiti, piemēram',
    'lof.lv link to the race': 'sacensību lof.lv saite',
    'Or a saved results page: on lof.lv choose': 'Vai saglabāta rezultātu lapa: lof.lv izvēlies',
    'and save it as PDF or as a web page, or copy the whole page (Ctrl+A, Ctrl+C) and paste it below.':
      'un saglabā to kā PDF vai tīmekļa lapu, vai nokopē visu lapu (Ctrl+A, Ctrl+C) un ielīmē zemāk.',
    '…or paste the results page here': '…vai ielīmē rezultātu lapu šeit',
    'Import and save results': 'Importēt un saglabāt rezultātus', 'Clear': 'Notīrīt',
    'Empty the link, the chosen file and the pasted text': 'Iztukšot saiti, izvēlēto failu un ielīmēto tekstu',
    'Replace results': 'Aizstāt rezultātus', 'Results with splits': 'Rezultāti ar starplaikiem',
    'not imported yet (Import results, under Runs)': 'vēl nav importēti (Importēt rezultātus, sadaļā Skrējieni)',
    'Paste the lof.lv link, choose the results file or paste the page.': 'Ielīmē lof.lv saiti, izvēlies rezultātu failu vai ielīmē lapu.',
    'Results for this map are not added yet.': 'Šīs kartes rezultāti vēl nav pievienoti.',
    "Results for this map have not been added yet, so runs can't be saved. Try again later.":
      'Šīs kartes rezultāti vēl nav pievienoti, tāpēc skrējienus vēl nevar saglabāt. Mēģini vēlāk.',
    'No results imported yet. Import them once; then runners only add their TCX and choose their name.':
      'Rezultāti vēl nav importēti. Importē tos vienreiz; pēc tam skrējēji tikai pievieno savu TCX un izvēlas savu vārdu.',
    'Add your run': 'Pievieno savu skrējienu', '1. Your watch file (TCX)': '1. Tava pulksteņa fails (TCX)',
    'How do I get my TCX file?': 'Kā iegūt savu TCX failu?',
    'Garmin watches: start a': 'Garmin pulksteņi: sāc aktivitāti', 'Run': 'Run',
    'activity, not Indoor Run, Treadmill or any other run. Only': ', nevis Indoor Run, Treadmill vai citu skrējienu. Tikai',
    'records the altitude that shows which floor you were on.': 'ieraksta augstumu, pēc kura redzams, kurā stāvā biji.',
    'Garmin Connect (website, on a computer)': 'Garmin Connect (vietne, datorā)', 'Sign in at': 'Pieslēdzies',
    'and open the activity.': 'un atver aktivitāti.',
    'Click the gear icon ⚙ at the top right of the activity.': 'Noklikšķini uz zobrata ikonas ⚙ aktivitātes augšējā labajā stūrī.',
    'Choose': 'Izvēlies', 'Export to TCX': 'Export to TCX', '. The file is saved to your Downloads.': '. Fails tiek saglabāts mapē Lejupielādes.',
    'Garmin Connect app (phone)': 'Garmin Connect lietotne (telefonā)', 'The app cannot export files. Open': 'Lietotne nevar eksportēt failus. Tā vietā atver',
    "in your phone's browser instead (choose “desktop site” if needed) and follow the steps above.":
      'telefona pārlūkā (ja vajag, izvēlies “datora versija”) un izpildi soļus augstāk.',
    'Open your activity on strava.com and add': 'Atver savu aktivitāti strava.com un pievieno',
    'to the end of its address, for example': 'tās adreses beigās, piemēram',
    '. Works only for your own activities.': '. Darbojas tikai ar tavām aktivitātēm.',
    'Should work if you can get a TCX file (for example through Strava), but it has not been tested yet.':
      'Vajadzētu darboties, ja vari iegūt TCX failu (piemēram, caur Strava), bet tas vēl nav pārbaudīts.',
    'Tested with Garmin watches.': 'Pārbaudīts ar Garmin pulksteņiem.',
    '2. Whose run is this?': '2. Kura skrējiens tas ir?', 'Runner': 'Skrējējs', 'Someone else': 'Kāds cits',
    'No results imported for this map yet. As admin you can add a run with typed split times.':
      'Šai kartei vēl nav importēti rezultāti. Kā administrators vari pievienot skrējienu ar ierakstītiem starplaikiem.',
    'Split times (the results line, leg times or elapsed times)': 'Starplaiki (rezultātu rinda, posmu laiki vai kopējie laiki)',
    'Save my run': 'Saglabāt manu skrējienu', 'Choose your TCX file first.': 'Vispirms izvēlies savu TCX failu.',
    'Choose your name, then press Save my run.': 'Izvēlies savu vārdu un spied “Saglabāt manu skrējienu”.',
    'Choose your name below; the results closest to this time are first.': 'Izvēlies savu vārdu zemāk; rezultāti, kas vistuvāk šim laikam, ir pirmie.',
    'Reading your watch file…': 'Nolasa pulksteņa failu…', 'Watch recording:': 'Pulksteņa ieraksts:',
    'Choose the activity from the race day.': 'Izvēlies aktivitāti no sacensību dienas.',
    'No result on this course fits this time. The watch must have run for the whole race: at most 30 s shorter or 10 min longer than the result.':
      'Neviens šīs distances rezultāts neatbilst šim laikam. Pulkstenim jābūt ieslēgtam visas sacensības: ne vairāk kā 30 s īsāk vai 10 min ilgāk par rezultātu.',
    '✓ saved on the server': '✓ saglabāts serverī', 'Runs on this map': 'Skrējieni šajā kartē', 'Replay': 'Atskaņot',
    'with a': 'ar pogu', 'button when ready.': ', kad tas būs gatavs.',
    'Fill in the name and split times.': 'Ievadi vārdu un starplaikus.',
    'Tick runs to compare them in one replay.': 'Atzīmē skrējienus, lai tos salīdzinātu vienā atskaņojumā.',
    'Compare': 'Salīdzināt', 'Delete ticked runs': 'Dzēst atzīmētos skrējienus', 'Admin: delete the ticked runs': 'Administrators: dzēst atzīmētos skrējienus',
    'Delete': 'Dzēst', 'Delete this run': 'Dzēst šo skrējienu', 'Sure? delete': 'Tiešām? dzēst', 'Run deleted.': 'Skrējiens izdzēsts.',
    'Tick the runs to delete first.': 'Vispirms atzīmē dzēšamos skrējienus.', 'Tick one or more runs first.': 'Vispirms atzīmē vienu vai vairākus skrējienus.',
    'Only finished runs can be compared: untick the others.': 'Salīdzināt var tikai pabeigtus skrējienus: noņem atzīmi pārējiem.',
    'No runs yet.': 'Vēl nav skrējienu.', 'none yet': 'vēl nav', 'Analyzing this run.': 'Analizē šo skrējienu.',
    'Waiting for its turn.': 'Gaida savu kārtu.', 'Waiting for its turn…': 'Gaida savu kārtu…',
    'Another map or run is being worked on; this one starts after it.': 'Pašlaik apstrādā citu karti vai skrējienu; šis sāksies pēc tā.',
    'Click again to delete for good': 'Noklikšķini vēlreiz, lai dzēstu pavisam',
    // map status / publishing
    'This map is being prepared. It takes a minute or two.': 'Karte tiek sagatavota. Tas aizņem minūti vai divas.',
    'Preparing this map in the background. You can leave this page; it carries on.': 'Karte tiek sagatavota fonā. Vari aizvērt šo lapu; darbs turpināsies.',
    'This map could not be prepared.': 'Šo karti neizdevās sagatavot.',
    'This map is still being checked. Runs can be added once it\'s fixed.': 'Šī karte vēl tiek pārbaudīta. Skrējienus varēs pievienot, kad tā būs salabota.',
    'Map prepared. Runners can add their runs.': 'Karte sagatavota. Skrējēji var pievienot savus skrējienus.',
    'The map is read, but some legs have no route. Fix the reading (Controls / Stairs / Dead ends) and save, or read the map again.':
      'Karte nolasīta, bet dažiem posmiem nav maršruta. Izlabo nolasīto (Kontrolpunkti / Kāpnes / Strupceļi) un saglabā vai nolasi karti vēlreiz.',
    'Published: everyone can see this map now.': 'Publicēta: tagad šo karti redz visi.',
    'Unpublished: only you can see this map now.': 'Nepublicēta: tagad šo karti redzi tikai tu.',
    'When you have checked it, publish it for everyone.': 'Kad esi to pārbaudījis, publicē to visiem.',
    'You can publish it once it is prepared.': 'To varēs publicēt, kad tā būs sagatavota.',
    'Fix the legs without a route first.': 'Vispirms izlabo posmus bez maršruta.',
    'Publish': 'Publicēt', 'Unpublish': 'Atcelt publicēšanu', 'Published': 'Publicēta', 'Draft': 'Melnraksts',
    'Read the map': 'Nolasīt karti', 'Read the map again': 'Nolasīt karti vēlreiz', 'Rebuild routes': 'Pārbūvēt maršrutus',
    'Redo all runs': 'Pārrēķināt visus skrējienus', 'Reading the map again.': 'Karte tiek nolasīta vēlreiz.',
    'Reading the map again. About a minute; saved runs are redone after.': 'Karte tiek nolasīta vēlreiz. Apmēram minūte; pēc tam saglabātie skrējieni tiek pārrēķināti.',
    'Rebuilding the routes.': 'Maršruti tiek pārbūvēti.',
    'Read again: reads the PDF from scratch (replaces manual fixes). Rebuild: keeps your fixes, builds routes again. Both redo the saved runs afterwards.':
      'Nolasīt vēlreiz: nolasa PDF no jauna (aizstāj manuālos labojumus). Pārbūvēt: saglabā tavus labojumus un veido maršrutus no jauna. Abos gadījumos pēc tam saglabātie skrējieni tiek pārrēķināti.',
    'Reads the PDF from scratch (floors, letters, controls). Your manual fixes are replaced; imported results and saved runs are kept, and runs are worked out again.':
      'Nolasa PDF no jauna (stāvus, burtus, kontrolpunktus). Tavi manuālie labojumi tiek aizstāti; importētie rezultāti un saglabātie skrējieni paliek, un skrējieni tiek pārrēķināti.',
    'Build the route network': 'Veidot maršrutu tīklu', 'Check every leg': 'Pārbaudīt katru posmu', 'Work out route options': 'Aprēķināt maršrutu variantus',
    'Route check per leg': 'Maršruta pārbaude pa posmiem', 'Route suggestions': 'Maršrutu ieteikumi', 'fastest way for every leg': 'ātrākais ceļš katram posmam',
    'Save and rebuild': 'Saglabāt un pārbūvēt', 'Saved. Rebuilding the route network…': 'Saglabāts. Maršrutu tīkls tiek pārbūvēts…',
    'You changed the map reading. Save to rebuild the route network (about a minute).': 'Tu mainīji kartes nolasījumu. Saglabā, lai pārbūvētu maršrutu tīklu (apmēram minūte).',
    'Strict stairs: into red only up, into blue only down; black lines round the stairs are walls (4. posms style)':
      'Stingrās kāpnes: sarkanajās tikai augšup, zilajās tikai lejup; melnās līnijas ap kāpnēm ir sienas (4. posma stils)',
    'Strict stairs on. Rebuilding the routes…': 'Stingrās kāpnes ieslēgtas. Maršruti tiek pārbūvēti…',
    'Standard stair reading. Rebuilding the routes…': 'Parastais kāpņu nolasījums. Maršruti tiek pārbūvēti…',
    'Delete map and its runs': 'Dzēst karti un tās skrējienus', 'Version': 'Versija', 'App updated': 'Lietotne atjaunināta',
    // map editing
    'Course order as read from the course lines. Click a row to find it; Move, then click the map to place it.':
      'Distances secība, kā nolasīta no distances līnijām. Noklikšķini uz rindas, lai to atrastu; “Pārvietot”, tad noklikšķini kartē, lai to novietotu.',
    'Add control (click map)': 'Pievienot kontrolpunktu (klikšķis kartē)', 'Move': 'Pārvietot', 'Start': 'Starts', 'Finish': 'Finišs',
    'Control': 'Kontrolpunkts', 'Controls:': 'Kontrolpunkti:', 'Start and finish can only be moved.': 'Startu un finišu var tikai pārvietot.',
    'Click the new control on the map. It is added before the finish; use the arrows to move it in the order.':
      'Noklikšķini uz jaunā kontrolpunkta kartē. Tas tiek pievienots pirms finiša; ar bultām pārvieto to secībā.',
    'Stairwell letters per floor. Red flights go up from that floor, blue flights go down; the same letter on different floors is one stairwell. Each flight is got on only where it meets the floor; the stair drawing itself (and an orange landing between the halves) is never used to change sides.':
      'Kāpņu telpu burti pa stāviem. Sarkanie marši ved augšup no šī stāva, zilie lejup; viens un tas pats burts dažādos stāvos ir viena kāpņu telpa. Uz marša var uzkāpt tikai tur, kur tas sakļaujas ar grīdu; pašu kāpņu zīmējumu (un oranžo laukumu starp pusēm) nekad neizmanto, lai pārietu uz otru pusi.',
    'Add letter (click map)': 'Pievienot burtu (klikšķis kartē)', 'Click next to the stair symbol where the letter is.': 'Noklikšķini blakus kāpņu zīmei, kur atrodas burts.',
    'Type the letter in the Stairs list.': 'Ieraksti burtu kāpņu sarakstā.', 'Floors found:': 'Atrastie stāvi:',
    'One-way stretches (green arrows) and four-way crossings (straight on only).': 'Vienvirziena posmi (zaļās bultas) un krustojumi (tikai taisni).',
    'Add one-way (click start, then end)': 'Pievienot vienvirziena posmu (klikšķis sākumā, tad beigās)', 'Add crossing (click centre)': 'Pievienot krustojumu (klikšķis centrā)',
    'Click where the one-way stretch starts (the side you enter from).': 'Noklikšķini, kur sākas vienvirziena posms (puse, no kuras ieiet).',
    'Now click where it ends (the side you leave by).': 'Tagad noklikšķini, kur tas beidzas (puse, pa kuru iziet).',
    'Click the centre of the four-way crossing sign.': 'Noklikšķini krustojuma zīmes centrā.', 'Reverse': 'Apgriezt',
    'Controls that can only be reached through one stair flight (inside a stairwell or fenced off by tape). The route finder normally sees these from the walls; add one here when it doesn\'t.':
      'Kontrolpunkti, kuriem var piekļūt tikai pa vienu kāpņu maršu (kāpņu telpā vai norobežoti ar lenti). Maršrutu meklētājs tos parasti saprot pēc sienām; pievieno šeit, ja ne.',
    'None found on this map.': 'Šajā kartē nav atrasts.', 'None needed so far.': 'Līdz šim nav vajadzīgs.', 'Apply': 'Lietot',
    'Floors': 'Stāvi', 'Floor': 'Stāvs', 'Legs': 'Posmi', 'Created': 'Izveidota',
    // replay page
    'Add TCX': 'Pievienot TCX', 'Open map': 'Atvērt karti', 'Drop map or TCX': 'Ievelc karti vai TCX',
    'Example map and runs. Open your map (PDF or image) and add your TCX file to replace it.': 'Piemēra karte un skrējieni. Atver savu karti (PDF vai attēlu) un pievieno savu TCX failu, lai to aizstātu.',
    'Example · Anna': 'Piemērs · Anna', 'Example · Jānis': 'Piemērs · Jānis', 'EXAMPLE · Floor 1 · 1:500': 'PIEMĒRS · 1. stāvs · 1:500',
    'Play': 'Atskaņot', 'Pause': 'Pauze', 'Faster': 'Ātrāk', 'Slower': 'Lēnāk', 'Faster (+)': 'Ātrāk (+)', 'Slower (−)': 'Lēnāk (−)',
    'Replay speed': 'Atskaņošanas ātrums', 'Replay time. Arrow keys step 1 s, Shift+arrow 5 s': 'Atskaņošanas laiks. Bultas pārlec 1 s, Shift+bulta 5 s',
    'Fit map': 'Ietilpināt karti', 'Zoom in': 'Pietuvināt', 'Zoom out': 'Attālināt', 'Follow': 'Sekot', 'Follow runner': 'Sekot skrējējam',
    'View': 'Skats', 'Display': 'Attēlojums', 'Mode': 'Režīms', 'Runners': 'Skrējēji', 'Selected runner': 'Izvēlētais skrējējs',
    'Route': 'Maršruts', 'Route shown': 'Rādītais maršruts', 'Whole race': 'Visas sacensības', 'Current leg': 'Pašreizējais posms',
    'How the route appears': 'Kā maršruts parādās', 'Appears as run': 'Parādās skrējiena gaitā', 'Show all': 'Rādīt visu',
    'Tail:': 'Aste:', 'Common start': 'Kopīgs starts', 'Real clock time': 'Īstais pulksteņa laiks', 'Start timing': 'Laika sākums',
    'Fade other floors': 'Citus stāvus blāvināt', 'Floor spacing': 'Attālums starp stāviem',
    'Analyze': 'Analizēt', 'Analyze a leg': 'Analizēt posmu', 'Leg to analyze': 'Analizējamais posms', 'Analyze routes on the map': 'Analizēt maršrutus kartē',
    'Every sensible route for this leg, timed at the runner\'s pace (or a standard pace when there is no runner). Press play to race them. Red is always the fastest.':
      'Visi saprātīgie šī posma maršruti ar skrējēja tempu (vai standarta tempu, ja skrējēja nav). Spied “Atskaņot”, lai tie sacenstos. Sarkanais vienmēr ir ātrākais.',
    ': every sensible route for each leg at a standard pace. Red is the fastest.': ': visi saprātīgie katra posma maršruti ar standarta tempu. Sarkanais ir ātrākais.',
    'At a standard running pace.': 'Ar standarta skriešanas tempu.', 'Finding every route…': 'Meklē visus maršrutus…',
    'No route found for this leg.': 'Šim posmam maršruts nav atrasts.', 'Fastest found:': 'Ātrākais atrastais:',
    'Show the fastest route of every leg': 'Rādīt katra posma ātrāko maršrutu',
    'Leg times (control to control)': 'Posmu laiki (no KP līdz KP)', 'Elapsed time at each control': 'Kopējais laiks katrā KP',
    'Split': 'Starplaiks', 'Split format': 'Starplaiku formāts', 'Lap': 'Aplis', 'Time': 'Laiks', 'Pace': 'Temps', 'Speed': 'Ātrums',
    'Distance': 'Attālums', 'Side': 'Puse', 'Top': 'Augša', 'Jump': 'Pārlēkt', 'Hide': 'Paslēpt', 'Hide this note': 'Paslēpt šo piezīmi',
    'Reset': 'Atiestatīt', 'Cancel': 'Atcelt', 'Add': 'Pievienot', 'All': 'Visi', 'Point': 'Punkts', 'Page': 'Lapa',
    'Map page / floor': 'Kartes lapa / stāvs', 'Map scale ≈': 'Kartes mērogs ≈', 'px per metre.': 'px uz metru.',
    'Rendering map…': 'Zīmē karti…', 'Loaded': 'Ielādēts', 'Last activity': 'Pēdējā aktivitāte',
    'No runs loaded. Use “Add TCX” or drop a file on the page.': 'Nav ielādētu skrējienu. Izmanto “Pievienot TCX” vai ievelc failu lapā.',
    'Add a TCX first': 'Vispirms pievieno TCX', 'Add your run below, or open a saved run to replay it.': 'Pievieno savu skrējienu zemāk vai atver saglabātu skrējienu, lai to atskaņotu.',
    'Align GPS': 'Pielāgot GPS', 'Align:': 'Pielāgot:', 'Alignment': 'Pielāgošana', 'Allow stretch (3+ points)': 'Atļaut izstiept (3+ punkti)',
    'Clear points': 'Notīrīt punktus', 'Undo point': 'Atsaukt punktu', '0 points': '0 punkti',
    'Use current time as this runner\'s start': 'Izmantot pašreizējo laiku kā šī skrējēja startu',
    'Use current time as this runner\'s finish': 'Izmantot pašreizējo laiku kā šī skrējēja finišu',
    'Set start here': 'Starts šeit', 'Set finish here': 'Finišs šeit',
    'No points yet. The track is placed roughly in the middle of the map. Switch to Align to pin it.':
      'Vēl nav punktu. Trase novietota aptuveni kartes vidū. Pārslēdz uz “Pielāgot”, lai to piestiprinātu.',
    'Shift only. Add a second point far from the first to fix rotation and scale.': 'Tikai nobīde. Pievieno otru punktu tālu no pirmā, lai noteiktu pagriezienu un mērogu.',
    'move the time to a moment you know (a control punch, the start, a stairway), then click where the runner really was. 1 point moves the track, 2 also rotate and scale it, 3+ average out GPS error.':
      'pārvieto laiku uz brīdi, ko zini (KP atzīmēšana, starts, kāpnes), tad noklikšķini, kur skrējējs tiešām bija. 1 punkts pārvieto trasi, 2 to arī pagriež un mērogo, 3+ izlīdzina GPS kļūdu.',
    'Saved in this browser for this map, so the next GPX from it lines up automatically.': 'Saglabāts šajā pārlūkā šai kartei, lai nākamais GPX tajā sakristu automātiski.',
    'Drawn routes are already on the map. Select a GPS runner to align.': 'Zīmētie maršruti jau ir kartē. Izvēlies GPS skrējēju, ko pielāgot.',
    'Draw a run': 'Zīmēt skrējienu', 'Draw route': 'Zīmēt maršrutu', 'Draw:': 'Zīmēt:', 'Drawn run': 'Zīmēts skrējiens',
    'Click adds': 'Klikšķis pievieno', 'Bend': 'Līkums', 'Click the start on the map.': 'Noklikšķini uz starta kartē.',
    'Start placed. Keep clicking along your route.': 'Starts novietots. Turpini klikšķināt pa savu maršrutu.',
    'Draw the route first: click the start, then along the way you ran.': 'Vispirms uzzīmē maršrutu: noklikšķini uz starta, tad pa ceļu, kā skrēji.',
    'start at the start triangle and click along the way you ran. Set “Click adds” to Control when you click a control, or tap a placed point to switch it. When you change floor, pick Stairs and click the same stairway on the other floor. The last point is the finish.':
      'sāc pie starta trīsstūra un klikšķini pa ceļu, kā skrēji. Kad klikšķini uz KP, iestati “Klikšķis pievieno” uz Kontrolpunkts vai pieskaries novietotam punktam, lai to pārslēgtu. Kad maini stāvu, izvēlies Kāpnes un noklikšķini uz tām pašām kāpnēm otrā stāvā. Pēdējais punkts ir finišs.',
    'Runner / run name': 'Skrējējs / skrējiena nosaukums', 'Split times, one per control including finish': 'Starplaiki, pa vienam katram KP, ieskaitot finišu',
    'Add split times or one total time.': 'Pievieno starplaikus vai vienu kopējo laiku.', 'Elapsed times must increase.': 'Kopējiem laikiem jāpieaug.',
    'Paste leg times, an elapsed-time row, or a whole results line.': 'Ielīmē posmu laikus, kopējo laiku rindu vai visu rezultātu rindu.',
    'Create replay': 'Izveidot atskaņojumu', 'Used for drawn routes: 1 cm on the map =': 'Zīmētiem maršrutiem: 1 cm kartē =',
    'Export to TCX': 'Eksportēt uz TCX', '3D view unavailable': '3D skats nav pieejams', 'The 3D view could not start:': '3D skatu neizdevās palaist:',
    'PDF viewer did not load (no internet?). Export the map as PNG/JPG and open that instead.': 'PDF skatītājs neielādējās (nav interneta?). Eksportē karti kā PNG/JPG un atver to.',
    '(Image maps assume 150 dpi.)': '(Attēlu kartēm pieņemts 150 dpi.)', 'Could not load:': 'Neizdevās ielādēt:', 'Could not open map:': 'Neizdevās atvērt karti:',
    'Could not read': 'Neizdevās nolasīt', ': no timestamps, using 1 s per point': ': nav laika zīmogu, pieņemta 1 s uz punktu',
    'This course has': 'Šai distancei ir', 'Needs': 'Vajag', 'Read total': 'Nolasīts kopējais laiks', 'leg times.': 'posmu laiki.',
    'Original': 'Oriģināls', 'Stairs:': 'Kāpnes:', 'same floor': 'tas pats stāvs', 'no stairs': 'bez kāpnēm',
    'AI Runner': 'AI skrējējs',

    'published': 'publicēta', 'waiting': 'gaida', 'not ready': 'nav gatava', 'Approve and publish': 'Apstiprināt un publicēt',
    'Everyone can see this map and add runs.': 'Šo karti redz visi un var pievienot skrējienus.',
    'Only you can see this map.': 'Šo karti redzi tikai tu.', 'Something failed.': 'Kaut kas neizdevās.',
    'no progress for a while: the job may have stopped. Try “Read the map again”.': 'kādu laiku nav progresa: darbs varētu būt apstājies. Mēģini “Nolasīt karti vēlreiz”.',
    'no route': 'nav maršruta', 'Select': 'Atzīmēt', 'Please wait until it is done; the': 'Lūdzu, uzgaidi, līdz tas būs gatavs; poga',
    'button appears here.': 'parādīsies šeit.', 'Earlier': 'Agrāk', 'Later': 'Vēlāk', 'code': 'kods',
    'flights found: up / down': 'atrastie marši: augšup / lejup', 'no flight': 'nav marša',
    'Show': 'Rādīt', 'Remove': 'Noņemt', 'Remove point': 'Noņemt punktu', 'As run': 'Kā skrēja', 'fastest': 'ātrākais',
    'Camera follows the runner': 'Kamera seko skrējējam', 'Look from the side': 'Skatīt no sāniem', 'Look from above': 'Skatīt no augšas',
    'Whole building': 'Visa ēka', 'Colour': 'Krāsa', 'of stairs': 'kāpņu',
    'Choose your name': 'Izvēlies savu vārdu', 'speed': 'ātrums', 'failed': 'neizdevās', 'done': 'gatavs', 'Done': 'Gatavs',
    'Site': 'Vietne', 'Moved to the TELPO site. It now shows only there.': 'Pārvietota uz TELPO vietni. Tagad tā redzama tikai tur.',
    'Moved to the main site. It now shows only there.': 'Pārvietota uz galveno vietni. Tagad tā redzama tikai tur.',

    // watch instructions under the map
    'How to get your TCX file': 'Kā iegūt savu TCX failu',
    "The route is worked out from your watch's": 'Maršruts tiek aprēķināts no pulksteņa',
    'altitude': 'augstuma', 'readings (they show which floor you were on), so the activity must record altitude. Choose your watch:':
      'mērījumiem (pēc tiem redzams, kurā stāvā biji), tāpēc aktivitātei jāieraksta augstums. Izvēlies savu pulksteni:',
    'Watch brand': 'Pulksteņa ražotājs', 'Tested': 'Pārbaudīts', 'Not tested yet': 'Vēl nav pārbaudīts', 'Any watch': 'Jebkurš pulkstenis',
    'Start a': 'Sāc aktivitāti', 'Get the file on a computer': 'Faila iegūšana datorā', 'Get the file': 'Faila iegūšana', 'On a phone': 'Telefonā',
    "The Garmin Connect app cannot export files. Open connect.garmin.com in your phone's browser (choose “desktop site” if needed) and follow the steps above.":
      'Garmin Connect lietotne nevar eksportēt failus. Atver connect.garmin.com telefona pārlūkā (ja vajag, izvēlies “datora versija”) un izpildi soļus augstāk.',
    'Use the': 'Izmanto', 'Running': 'Running',
    'sport mode, not Treadmill or another indoor mode. Indoor modes may not record altitude.': 'sporta režīmu, nevis Treadmill vai citu telpu režīmu. Telpu režīmi var neierakstīt augstumu.',
    'The Suunto app exports only FIT and GPX files, not TCX. The easiest way is through Strava:': 'Suunto lietotne eksportē tikai FIT un GPX failus, nevis TCX. Vienkāršākais ceļš ir caur Strava:',
    'In the Suunto app connect Strava once (under': 'Suunto lietotnē vienreiz pievieno Strava (sadaļā', 'Partner services': 'Partner services', 'in the app settings).': 'lietotnes iestatījumos).',
    'After the race the activity appears in Strava by itself.': 'Pēc sacensībām aktivitāte Strava parādīsies pati.',
    'Get the TCX file from Strava (see the Strava tab).': 'Lejupielādē TCX failu no Strava (skat. cilni Strava).',
    'Not tested with Suunto yet. If the site says the file has no altitude readings, the sport mode did not record altitude.': 'Ar Suunto vēl nav pārbaudīts. Ja vietne saka, ka failā nav augstuma mērījumu, sporta režīms neierakstīja augstumu.',
    'sport profile, not Treadmill running or Indoor running. Indoor profiles may not record altitude. The watch must have a barometer (altitude sensor).':
      'sporta profilu, nevis Treadmill running vai Indoor running. Telpu profili var neierakstīt augstumu. Pulkstenim jābūt barometram (augstuma sensoram).',
    'and open': 'un atver', 'Diary': 'Diary', 'Click the training session.': 'Noklikšķini uz treniņa.', 'Open the': 'Atver', 'Export': 'Export',
    'menu at the top right and choose': 'izvēlni augšā labajā stūrī un izvēlies', '. The file is saved to your Downloads.': '. Fails tiek saglabāts mapē Lejupielādes.',
    'Not tested with Polar yet. If the site says the file has no altitude readings, the sport profile did not record altitude.': 'Ar Polar vēl nav pārbaudīts. Ja vietne saka, ka failā nav augstuma mērījumu, sporta profils neierakstīja augstumu.',
    'In the Workout app choose': 'Lietotnē Workout izvēlies', 'Outdoor Run': 'Outdoor Run', ', not Indoor Run. Altitude is measured by Apple Watch Series 3 and newer, SE and Ultra.':
      ', nevis Indoor Run. Augstumu mēra Apple Watch Series 3 un jaunāki, SE un Ultra.',
    "Apple's Health and Fitness apps cannot export TCX files. The easiest way is through Strava:": 'Apple lietotnes Health un Fitness nevar eksportēt TCX failus. Vienkāršākais ceļš ir caur Strava:',
    "In the Strava phone app connect Apple Health once (in Strava's settings), or record the run with the Strava app on the watch.":
      'Strava telefona lietotnē vienreiz pievieno Apple Health (Strava iestatījumos) vai ieraksti skrējienu ar Strava lietotni pulkstenī.',
    'After the race the run appears in Strava.': 'Pēc sacensībām skrējiens parādīsies Strava.',
    'Not tested with Apple Watch yet. If the site says the file has no altitude readings, the workout did not record altitude.': 'Ar Apple Watch vēl nav pārbaudīts. Ja vietne saka, ka failā nav augstuma mērījumu, treniņš neierakstīja augstumu.',
    'Works for any watch whose activities go to Strava (Suunto, Apple Watch, Coros, Polar, Garmin…). The activity type on the watch still matters: use the outdoor':
      'Der jebkuram pulkstenim, kura aktivitātes nonāk Strava (Suunto, Apple Watch, Coros, Polar, Garmin…). Aktivitātes veids pulkstenī joprojām ir svarīgs: izmanto āra',
    ', not an indoor or treadmill run.': ', nevis telpu vai skrejceliņa skrējienu.',
    'Open your activity on': 'Atver savu aktivitāti', '(on a phone, open it in the browser, not the app).': '(telefonā atver to pārlūkā, nevis lietotnē).',
    'to the end of the address, for example': 'adreses beigās, piemēram', ', and press Enter.': ', un spied Enter.',
    'The TCX file is saved to your Downloads.': 'TCX fails tiek saglabāts mapē Lejupielādes.', 'Works only for your own activities.': 'Darbojas tikai ar tavām aktivitātēm.',
    'Then add the file above under': 'Pēc tam pievieno failu augstāk sadaļā', ', step 1. The site checks the file and tells you straight away if it has no altitude readings.':
      ', 1. solī. Vietne pārbauda failu un uzreiz pasaka, ja tajā nav augstuma mērījumu.',
    'How do I get my TCX file? Instructions for Garmin, Suunto, Polar, Apple Watch and Strava ↓': 'Kā iegūt savu TCX failu? Instrukcijas Garmin, Suunto, Polar, Apple Watch un Strava ↓',

    '1. Your watch file (TCX, FIT or GPX)': '1. Tava pulksteņa fails (TCX, FIT vai GPX)', 'How to get your watch file': 'Kā iegūt pulksteņa failu',
    'How do I get my watch file? Instructions for Garmin, Suunto, Polar, Apple Watch and Strava ↓': 'Kā iegūt pulksteņa failu? Instrukcijas Garmin, Suunto, Polar, Apple Watch un Strava ↓',
    'Get the file (Suunto app, on the phone)': 'Faila iegūšana (Suunto lietotnē, telefonā)', 'Open the workout in the Suunto app.': 'Atver treniņu Suunto lietotnē.',
    'Tap the three dots ⋯ at the top right and choose': 'Pieskaries trim punktiem ⋯ augšā labajā stūrī un izvēlies', 'FIT': 'FIT',
    'Save the file, then add it here.': 'Saglabā failu un pievieno to šeit.',
    'A workout recorded without GPS can only be exported as FIT, which is fine here. Through Strava works too (see the Strava tab).':
      'Treniņu, kas ierakstīts bez GPS, var eksportēt tikai kā FIT, un tas šeit der. Var arī caur Strava (skat. cilni Strava).',
    "Apple's Health and Fitness apps cannot export workout files. The easiest way is through Strava:": 'Apple lietotnes Health un Fitness nevar eksportēt treniņu failus. Vienkāršākais ceļš ir caur Strava:',
    ', step 1. TCX, FIT and GPX files all work. The site checks the file and tells you straight away if it has no altitude readings.':
      ', 1. solī. Der TCX, FIT un GPX faili. Vietne pārbauda failu un uzreiz pasaka, ja tajā nav augstuma mērījumu.',
    'Choose your watch file first.': 'Vispirms izvēlies savu pulksteņa failu.',
    'Choose your watch file (TCX, FIT or GPX).': 'Izvēlies savu pulksteņa failu (TCX, FIT vai GPX).', 'Add your watch file (TCX, FIT or GPX).': 'Pievieno savu pulksteņa failu (TCX, FIT vai GPX).',
    'Choose your watch file and whose run it is.': 'Izvēlies savu pulksteņa failu un to, kura skrējiens tas ir.',
    'Stages': 'Posmi', 'Maps coming soon': 'Kartes drīzumā', 'Information': 'Informācija', 'Participants': 'Dalībnieki', 'Register': 'Pieteikties',
    'Indoor orienteering races in Latvia. The joy of sport and adventures with friends and family.': 'Iekštelpu orientēšanās sacensības Latvijā. Sporta prieks un piedzīvojumi kopā ar draugiem un ģimeni.',
    'Legal information': 'Juridiskā informācija', 'Terms of use': 'Lietošanas noteikumi', 'Privacy policy': 'Privātuma politika', 'Cookie policy': 'Sīkdatņu politika',
    'Social networks': 'Sociālie tīkli', 'TELPO sākumlapa': 'TELPO sākumlapa',
    'Long': 'Garā', 'Medium': 'Vidējā', 'Easy': 'Vieglā', 'Long A': 'Garā A', 'Long B': 'Garā B', 'Medium A': 'Vidējā A', 'Medium B': 'Vidējā B',
    'Show maps': 'Rādīt kartes', 'Open map →': 'Atvērt karti →', 'Choose a distance': 'Izvēlies distanci', 'Back to the picture': 'Atpakaļ uz attēlu',
    'All maps (admin)': 'Visas kartes (administratoram)', 'Rules': 'Nolikums',
    'Long and Medium: two runs (A and B), the stage result is the sum of both times. Easy: one run.': 'Garā un Vidējā: divi skrējieni (A un B), posma rezultāts ir abu laiku summa. Vieglā: viens skrējiens.',
    // server messages
    'This needs the admin password.': 'Tam vajag administratora paroli.', 'Reading the map…': 'Nolasa karti…',
    'Building the route network…': 'Veido maršrutu tīklu…', 'Waiting for its turn (map was updated)…': 'Gaida savu kārtu (karte tika atjaunināta)…',
    'Rebuilding the route network…': 'Pārbūvē maršrutu tīklu…', 'Upload the map as a PDF file.': 'Augšupielādē karti kā PDF failu.',
    'The map is not ready yet.': 'Karte vēl nav gatava.', 'No 3D data for this map yet.': 'Šai kartei vēl nav 3D datu.',
    'The map is not ready yet. Publish it when it has been prepared.': 'Karte vēl nav gatava. Publicē to, kad tā būs sagatavota.',
    'The map is still being read. Try again in a minute.': 'Karte vēl tiek nolasīta. Mēģini pēc minūtes.',
    'The map is still being prepared. Try again in a minute.': 'Karte vēl tiek sagatavota. Mēģini pēc minūtes.',
    'No groups with split times found in this file. On lof.lv open the race, choose the Split view, and save that page as PDF (or copy the whole page and paste it).':
      'Šajā failā nav atrasta neviena grupa ar starplaikiem. lof.lv atver sacensības, izvēlies skatu “Split” un saglabā lapu kā PDF (vai nokopē visu lapu un ielīmē).',
    'Choose your TCX file.': 'Izvēlies savu TCX failu.', 'Bad leg or pace.': 'Nepareizs posms vai temps.',
    'Choose whose run this is.': 'Izvēlies, kura skrējiens tas ir.', 'Add your TCX watch file.': 'Pievieno savu TCX pulksteņa failu.',
    'The results for this map have not been added yet.': 'Šīs kartes rezultāti vēl nav pievienoti.',
    'That does not look like a lof.lv race link (it should end with ?id= and a number).': 'Tā neizskatās pēc lof.lv sacensību saites (tai jābeidzas ar ?id= un skaitli).',
    'Choose the results file (PDF) or paste the results text.': 'Izvēlies rezultātu failu (PDF) vai ielīmē rezultātu tekstu.',
    'Choose…': 'Izvēlies…'
  };

  /* texts with numbers or names in them: [pattern, Latvian] ($1… = the parts in brackets) */
  var P = [
    [/^© (\d{4}) TELPO\. All rights reserved\.$/, '© $1 TELPO. Visas tiesības aizsargātas.'],
    [/^(.*) has no altitude readings\. The activity must be an outdoor Run \(not an indoor or treadmill run\): see How to get your watch file under the map\.$/, '$1 nav augstuma mērījumu. Aktivitātei jābūt āra skrējienam (Run), nevis telpu vai skrejceliņa skrējienam: skat. “Kā iegūt pulksteņa failu” zem kartes.'],

    [/^⚠ Floor numbers read as (.*): one may be misread\. Check the Map tab\.$/, '⚠ Stāvu numuri nolasīti kā $1: kāds varētu būt nolasīts nepareizi. Pārbaudi cilni Karte.'],
    [/^(\d+) course symbol\(s\) could not be put in order; check the control list\.$/, '$1 distances zīmes neizdevās sakārtot; pārbaudi kontrolpunktu sarakstu.'],

    [/^Leg (\d+)$/, '$1. posms'],
    [/^Last activity (.*) ago$/, 'Pēdējā aktivitāte pirms $1'],
    [/^Used for drawn routes: 1 cm on the map = (.*) m\. Your GPS points suggest about 1:(\d+)\.$/, 'Zīmētiem maršrutiem: 1 cm kartē = $1 m. Tavi GPS punkti liecina par aptuveni 1:$2.'],
    [/^running since (.*)$/, 'darbojas kopš $1'],
    [/^running (.*)$/, 'darbojas $1'],
    [/^(\d+) maps?$/, function (m) { return m[1] + (m[1] === '1' ? ' karte' : ' kartes'); }],
    [/^(\d+) floors · (\d+) controls · (\d+) runs?$/, function (m) { return m[1] + ' stāvi · ' + m[2] + ' KP · ' + m[3] + (m[3] === '1' ? ' skrējiens' : ' skrējieni'); }],
    [/^(\d+) leg\(s\) without a route$/, '$1 posmi bez maršruta'],
    [/^Floors (.*) · (\d+) controls$/, 'Stāvi $1 · $2 KP'],
    [/^Floor (-?\d+)$/, '$1. stāvs'],
    [/^leg (\d+) of (.+?)( · running .*)?$/, function (m) { return 'posms ' + m[1] + ' no ' + m[2] + (m[3] ? ' · darbojas ' + m[3].replace(' · running ', '') : ''); }],
    [/^floor (\d+) of (.+?) · running (.*)$/, 'stāvs $1 no $2 · darbojas $3'],
    [/^routes found for all (\d+) legs$/, 'maršruti atrasti visiem $1 posmiem'],
    [/^routes found for (\d+) of (\d+) legs: no route for leg (.*)$/, 'maršruti atrasti $1 no $2 posmiem; nav maršruta posmam $3'],
    [/^(\d+) options over (\d+) legs$/, '$1 varianti $2 posmos'],
    [/^(.*) · (\d+) runners in (.*)$/, '$1 · $2 skrējēji grupās $3'],
    [/^(\d+) runners in (.*)$/, '$1 skrējēji grupās $2'],
    [/^Saved: (\d+) runners in (.*)$/, 'Saglabāts: $1 skrējēji grupās $2'],
    [/^(\d+) saved$/, '$1 saglabāti'], [/^(\d+) done$/, '$1 gatavi'], [/^(\d+) being worked out$/, '$1 tiek aprēķināti'],
    [/^(\d+) failed$/, '$1 neizdevās'], [/^(\d+) with legs missing$/, '$1 ar trūkstošiem posmiem'],
    [/^Working out (\d+) runs? again\.$/, '$1 skrējienu(s) pārrēķina.'],
    [/^Sure\? delete (\d+) runs?$/, 'Tiešām? dzēst $1 skrējienu(s)'],
    [/^(\d+) runs? deleted\.$/, '$1 skrējien(s) izdzēsts(i).'],
    [/^(\d+) times found; this course has (\d+) legs \(controls \+ finish\)\.$/, 'Atrasti $1 laiki; šai distancei ir $2 posmi (KP + finišs).'],
    [/^This course has (\d+) legs\.$/, 'Šai distancei ir $1 posmi.'],
    [/^(.*) already has a saved run here\. Saving replaces it\.$/, '$1 šeit jau ir saglabāts skrējiens. Saglabājot tas tiek aizstāts.'],
    [/^Press$/, 'Spied'], [/^to save it as$/, ', lai to saglabātu kā'],
    [/^(.*)'s run\. The route is being worked out now \(1–2 minutes\); it appears under$/, '$1 skrējiens. Maršruts tiek aprēķināts (1–2 minūtes); tas parādīsies sadaļā'],
    [/^This watch file is from (.*), but the race was on (.*)\. Choose the activity from the race day\.$/, 'Šis pulksteņa fails ir no $1, bet sacensības bija $2. Izvēlies aktivitāti no sacensību dienas.'],
    [/^Click where (.*) is\.$/, 'Noklikšķini, kur atrodas $1.'],
    [/^Floors found: (.*)$/, 'Atrastie stāvi: $1'],
    [/^(\S+) up from floor (-?\d+)$/, '$1 augšup no $2. stāva'], [/^(\S+) down from floor (-?\d+)$/, '$1 lejup no $2. stāva'],
    [/^only via (.*)$/, 'tikai pa $1'], [/^Suggestion \((.*)\): only via (.*)\?$/, 'Ieteikums ($1): tikai pa $2?'],
    [/^One-way, floor (-?\d+)$/, 'Vienvirziena, $1. stāvs'], [/^Straight on only, floor (-?\d+)$/, 'Tikai taisni, $1. stāvs'],
    [/^App updated (.*)$/, 'Lietotne atjaunināta $1'], [/^· running since (.*)$/, '· darbojas kopš $1'], [/^Version (.*?): (.*)$/, 'Versija $1: $2'],
    [/^Moved to folder (.*)$/, 'Pārvietota uz mapi $1'],
    [/^(.*): GPS signal was lost for (.*) in total\. Gaps are left blank on the map\.$/, '$1: GPS signāls kopumā pazuda uz $2. Pārtraukumi kartē paliek tukši.'],
    [/^Loaded (\d+) saved alignment point\(s\) for this map$/, 'Ielādēti $1 saglabāti pielāgošanas punkti šai kartei'],
    [/^Fastest found: (.*), about (.*)$/, 'Ātrākais atrastais: $1, apmēram $2'],
    [/^Used for drawn routes: 1 cm on the map = (.*) m\.$/, 'Zīmētiem maršrutiem: 1 cm kartē = $1 m.'],
    [/^Your GPS points suggest about 1:(\d+)\.$/, 'Tavi GPS punkti liecina par aptuveni 1:$1.'],
    [/^Point (\d+) set: (.*) at (.*)$/, '$1. punkts iestatīts: $2 laikā $3'],
    [/^(\d+) points · (\d+) controls \+ finish · (.*)$/, '$1 punkti · $2 KP + finišs · $3'],
    [/^Read (\d+) leg times from a results row \(checked against elapsed times\)\.$/, 'Nolasīti $1 posmu laiki no rezultātu rindas (pārbaudīti pret kopējiem laikiem).'],
    [/^Read total (.*) \+ (\d+) leg times\.$/, 'Nolasīts kopējais laiks $1 + $2 posmu laiki.'],
    [/^Needs (\d+) times \(one per control plus finish\), or one total time\.$/, 'Vajag $1 laikus (pa vienam katram KP un finišam) vai vienu kopējo laiku.'],
    [/^(\d+) times, total (.*)\.$/, '$1 laiki, kopā $2.'], [/^Your drawing has (\d+) legs\.$/, 'Tavā zīmējumā ir $1 posmi.'],
    [/^You drew (\d+) legs but gave (\d+) times\. Tap a point to toggle it as a control, or fix the splits\.$/, 'Tu uzzīmēji $1 posmus, bet ievadīji $2 laikus. Pieskaries punktam, lai to pārslēgtu par KP, vai izlabo starplaikus.'],
    [/^Created (.*)\. Press play\.$/, 'Izveidots $1. Spied “Atskaņot”.'],
    [/^off by (.*) m$/, 'novirze $1 m'],
    [/^(.*) has no GPS points \(recorded indoors\)\.(.*)$/, '$1 nav GPS punktu (ierakstīts telpās).$2'],
    [/^Checking leg (\d+) of (\d+)…$/, 'Pārbauda $1. posmu no $2…'], [/^Route options: leg (\d+) of (\d+)…$/, 'Maršrutu varianti: $1. posms no $2…'],
    [/^Building the route network: (.*)$/, 'Veido maršrutu tīklu: $1'],
    [/^This course has (\d+) legs \(controls \+ finish\) but I read (\d+) times \((.*)\)\.$/, 'Šai distancei ir $1 posmi (KP + finišs), bet nolasīti $2 laiki ($3).'],
    [/^Some legs have no route \((.*)\)\. Fix them before publishing\.$/, 'Dažiem posmiem nav maršruta ($1). Izlabo tos pirms publicēšanas.'],
    [/^No group in these results ran this course \((\d+) legs\)\. Found (.*)\.$/, 'Neviena grupa šajos rezultātos neskrēja šo distanci ($1 posmi). Atrasts: $2.'],
    [/^No group in these results ran this map's course \((.*)\)\. Found (.*)\.$/, 'Neviena grupa šajos rezultātos neskrēja šīs kartes distanci ($1). Atrasts: $2.'],
    [/^Could not read the results: (.*)$/, 'Neizdevās nolasīt rezultātus: $1'],
    [/^Could not get the results from lof\.lv: (.*)$/, 'Neizdevās iegūt rezultātus no lof.lv: $1'],
    [/^(.*) is not in the results of group (.*)\.$/, '$1 nav grupas $2 rezultātos.'],
    [/^(.*) has no complete split times \((.*)\), so the route can't be worked out\.$/, '$1 nav pilnu starplaiku ($2), tāpēc maršrutu nevar aprēķināt.'],
    [/^An identical run is already assigned to (.*) \((.*)\)\. Nothing was added\.$/, '$1 ($2) jau ir piešķirts identisks skrējiens. Nekas netika pievienots.'],
    [/^Not saved: (.*)$/, function (m) { return 'Nav saglabāts: ' + tr(m[1]); }],
    [/^(.*) · Replay$/, '$1 · Atskaņojums'], [/^(.*) · Route options$/, '$1 · Maršrutu varianti'],
    [/^(\d+) · \+(.*)$/, '$1 · +$2']
  ];

  var orig = new WeakMap();      // node → its English text
  var attrs = ['placeholder', 'title', 'aria-label'];
  var origAttr = new WeakMap();  // element → {attr: English}

  function tr(s) {
    var t = s.trim(); if (!t) return s;
    var out = null;
    if (Object.prototype.hasOwnProperty.call(D, t)) out = D[t];
    else for (var i = 0; i < P.length; i++) {
      var m = t.match(P[i][0]);
      if (m) { out = typeof P[i][1] === 'function' ? P[i][1](m) : t.replace(P[i][0], P[i][1]); break; }
    }
    if (out == null && t.indexOf(' · ') > 0) {            // "3 saved · 2 done", "Leg 1 · same floor · 0:34"
      var parts = t.split(' · '), any = false;
      for (var k = 0; k < parts.length; k++) { var q = tr(parts[k]); if (q !== parts[k]) { parts[k] = q; any = true; } }
      if (any) out = parts.join(' · ');
    }
    if (out == null) return s;
    var lead = s.match(/^\s*/)[0], tail = s.match(/\s*$/)[0];
    return lead + out + tail;
  }
  window.T = function (s) { return lang === 'lv' ? tr(String(s)) : String(s); };
  window.LANG = lang;

  var busy = false;
  function doText(n) {
    var cur = n.nodeValue, o = orig.get(n);
    if (o != null && (cur === o || cur === tr(o))) {           // ours: set to the chosen language
      var want = lang === 'lv' ? tr(o) : o;
      if (cur !== want) n.nodeValue = want;
      return;
    }
    orig.set(n, cur);                                          // new English text from the page
    if (lang === 'lv') { var t = tr(cur); if (t !== cur) n.nodeValue = t; }
  }
  function doEl(el) {
    var rec = origAttr.get(el) || {};
    for (var i = 0; i < attrs.length; i++) {
      var a = attrs[i]; if (!el.hasAttribute(a)) continue;
      var cur = el.getAttribute(a), o = rec[a];
      if (o == null || (cur !== o && cur !== tr(o))) { rec[a] = o = cur; }
      var want = lang === 'lv' ? tr(o) : o;
      if (cur !== want) el.setAttribute(a, want);
    }
    origAttr.set(el, rec);
  }
  function walk(root) {
    if (root.nodeType === 3) { if (!skip(root.parentNode)) doText(root); return; }
    if (root.nodeType !== 1 || skip(root)) return;
    doEl(root);
    var w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, null);
    var n; while ((n = w.nextNode())) {
      if (n.nodeType === 3) { if (!skip(n.parentNode)) doText(n); }
      else if (!skip(n)) doEl(n);
    }
  }
  function skip(el) {
    for (var e = el; e && e.nodeType === 1; e = e.parentNode) {
      var tg = e.tagName; if (tg === 'SCRIPT' || tg === 'STYLE' || tg === 'CODE' || tg === 'TEXTAREA') return true;
      if (e.hasAttribute && (e.hasAttribute('data-noi18n') || e.isContentEditable)) return true;
    }
    return false;
  }
  function run() { busy = true; walk(document.body); document.title = lang === 'lv' ? tr(origTitle) : origTitle; busy = false; }
  var origTitle = document.title;

  var mo = new MutationObserver(function (list) {
    if (busy) return; busy = true;
    for (var i = 0; i < list.length; i++) {
      var r = list[i];
      if (r.type === 'characterData') { if (!skip(r.target.parentNode)) doText(r.target); }
      else if (r.type === 'attributes') { if (!skip(r.target)) doEl(r.target); }
      else for (var j = 0; j < r.addedNodes.length; j++) walk(r.addedNodes[j]);
    }
    busy = false;
  });

  function flags() {
    var box = document.createElement('div'); box.className = 'langsw'; box.setAttribute('data-noi18n', '');
    box.innerHTML = '<button type="button" data-l="lv" title="Latviski" aria-label="Latviski">' + lvFlag + '</button>' +
                    '<button type="button" data-l="en" title="English" aria-label="English">' + gbFlag + '</button>';
    box.querySelectorAll('button').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.l === lang));
      b.onclick = function () {
        if (b.dataset.l === lang) return;
        lang = b.dataset.l; window.LANG = lang;
        try { localStorage.setItem(KEY, lang); } catch (e) {}
        document.documentElement.lang = lang;
        box.querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', String(x.dataset.l === lang)); });
        run(); window.dispatchEvent(new Event('langchange'));
      };
    });
    var h = document.querySelector('header');
    if (h) {
      var right = h.lastElementChild && h.lastElementChild !== h.firstElementChild ? h.lastElementChild : null;
      if (right) right.insertBefore(box, right.firstChild); else h.appendChild(box);
    } else { box.classList.add('float'); document.body.appendChild(box); }
    var st = document.createElement('style');
    st.textContent = '.langsw{display:inline-flex;gap:4px;align-items:center}.langsw button{border:1px solid transparent;background:none;padding:2px;border-radius:4px;cursor:pointer;line-height:0;opacity:.55}' +
      '.langsw button[aria-pressed=true]{opacity:1;border-color:var(--line,#ccc)}.langsw button:hover{opacity:1}.langsw svg{width:24px;height:16px;display:block;border-radius:2px;box-shadow:0 0 0 1px rgba(0,0,0,.15)}' +
      '.langsw.float{position:fixed;top:8px;right:8px;z-index:9999;background:var(--panel,#fff);padding:3px;border-radius:6px}';
    document.head.appendChild(st);
  }
  var lvFlag = '<svg viewBox="0 0 20 10" aria-hidden="true"><rect width="20" height="10" fill="#9E3039"/><rect y="4" width="20" height="2" fill="#fff"/></svg>';
  var gbFlag = '<svg viewBox="0 0 60 30" aria-hidden="true"><clipPath id="i18nT"><path d="M30,15h30v15zv15h-30zh-30v-15zv-15h30z"/></clipPath><path d="M0,0v30h60v-30z" fill="#012169"/><path d="M0,0 60,30M60,0 0,30" stroke="#fff" stroke-width="6"/><path d="M0,0 60,30M60,0 0,30" clip-path="url(#i18nT)" stroke="#C8102E" stroke-width="4"/><path d="M30,0v30M0,15h60" stroke="#fff" stroke-width="10"/><path d="M30,0v30M0,15h60" stroke="#C8102E" stroke-width="6"/></svg>';

  function start() {
    document.documentElement.lang = lang;
    flags(); run();
    mo.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: attrs });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
