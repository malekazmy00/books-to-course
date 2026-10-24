# Root review of every historically failed translation unit

Recorded UTC: 2026-10-09T13:45:17.915126+00:00

## Scope and evidence

Root personally read and compared the complete source and selected full-source response for all154 units in the accumulated failure union. The user's148 count was a prior snapshot; no unit was omitted because the live count changed. This is154 original units, not154 pages or749 independently reviewed provider calls. Reconstructed request envelopes establish full-source requests for153units; the historical request scope for `book-main:338:0` cannot be proven from the remaining cache. Its complete current source and historical target were read, with attribution explicitly uncertain.

The private source/response evidence is retained at `outputs/translation-diagnostics-2026-10-09/all-failed-units-scoped-private.json`; the individually recorded root judgments are in `root-personal-findings.json` in that directory. Both remain private. This report contains judgments/IDs, not complete copyrighted passages or credentials.

## Confirmed problems and required fixes

- Full requests sometimes produced only the heading or a partial opening paragraph. Input forwarding and8192output-token requests were independently traced; no local input truncation was found. Historical finish reasons were discarded, so provider termination cannot be asserted. Isolate headings/body, translate bounded source-preserving pieces and require complete reassembly before adoption.
- A bad response rejected the entire batch, including valid headings. Save validated original items independently, rebuild subsequent requests for unresolved IDs and never checkpoint an adaptive fragment as a whole original.
- PDF spacing joins years, footnotes, figure identifiers or prose to numbers. The old lexer falsely excluded some source numbers and partially protected decimal figure labels. Strengthen source-aware tokenization and exact ordered numeric checks; do not bypass changed/missing numbers.
- Genuine added/shifted numeric values, introduced HTML/citation wrappers and missing table cells occur. These require rejection and automatic recovery. Short table fragments evade the old200-character completeness floor; table cell sequence and count must be checked separately.
- Some latest full responses are faithful after earlier failure; preserve current valid checkpoints rather than retranslate every historical failed ID. After stronger checks, a previously accepted faulty table may correctly move back to retry without deleting its stored evidence.
- Page-boundary incomplete sentences are present in the source itself. Preserve their source scope; do not mislabel their translated equivalents as truncation or invent missing continuation.
- Remaining terminology/format issues are explicitly recorded. A zero operational-failure counter is not a claim of perfect semantic translation.

## Verification and publication

Root review154/154 is complete. Functional fixes are being prepared and tested; this record does not mark live translation complete or authorize skipping quality checks. Publish after focused regression checks and revalidation against this diagnostic corpus, preserve accepted immutable artifacts, then monitor the resumed live run through final coverage.

## Individual judgments

| Review | Unit | Finding | Specific comparison |
|---|---|---|---|
| 1 | `book-main:31:0` | body_omitted | Only ORIGIN AND PURPOSE heading; commissions2001,Iraq2002,2011 editions and all paragraphs omitted. |
| 2 | `book-main:34:0` | body_omitted | Audience heading only; practitioner and student paragraphs absent. |
| 3 | `book-main:59:0` | tail_omitted | Chapters8–10 mostly translated, chapter11 cuts afteropeningparenthesis andentirefutureparagraph omitted. Exactrequestbinding reveals2030 in sourceis2030 wasnotprotected; oldnumericgate+ratio>.45 falselypass. Addjoinedyear andbalancedbracketcoverage checks. HighImpact/LowProbability terminology also mistranslated. |
| 4 | `book-main:71:0` | valid_collateral | Title2.3 correctly translated; paired71:1 body omitted causes all-or-nothing rejection. |
| 5 | `book-main:71:1` | body_omitted | Title2.3 only; System1,1970s/1980s,1999 and biases/heuristics paragraphs absent. |
| 6 | `book-main:72:0` | numeric_validation_false_reject | Complete body and footnotes17–19; mixed preserved markers and raw System1 need exact ordered mixed restoration. |
| 7 | `book-main:75:1` | source_footnote_tokenization | Complete disinformation body; Latin-attached sourceDisinformation21 not recognized while Arabic-attached21 is recognized; normalize source-aware footnote identity. |
| 8 | `book-main:92:0` | valid_collateral | Selecting technique3.3 heading correct; paired92:1 omittedbody. |
| 9 | `book-main:92:1` | body_omitted | Selecting heading only; twelve tasks,six families,figure3.3A,2019 missing. |
| 10 | `book-main:94:0` | latest_full_response_preserved | 3.4 title complete; exact request/envelope contains only94:0and94:1,bothlatesttargets faithful. Earlier failure recovered; preservevalidcheckpoint. |
| 11 | `book-main:94:1` | latest_full_response_preserved | Exacttwo-IDresponse complete:66words,Delphi8,footnote1,ordered1/2 preserved. Terminal They exists in source too, not model truncation. Earlierfailure not prooflatestoutputfailed. |
| 12 | `book-main:104:0` | valid_collateral | 4.1 title correctly translated;104:1 omittedbody. |
| 13 | `book-main:104:1` | body_omitted | 4.1 header only; traditional,specialproject,socialnetwork definitions all absent. |
| 14 | `book-main:107:0` | valid_collateral | 4.2 title correct;107:1 omittedbody. |
| 15 | `book-main:107:1` | body_omitted | 4.2 heading only; divergent/convergent process,leader roles and techniques absent. |
| 16 | `book-main:117:0` | valid_collateral | 4.7 leadership title correct;117:1 missingbody. |
| 17 | `book-main:117:1` | body_omitted | 4.7 heading only; facilitator tasks,20/21 references and training paragraphs absent. |
| 18 | `book-main:128:0` | complete_with_format_artifact | Sorting,ranking,matrices,processmaps,Gantt,othertechniques all present; literal n separators require outputformat repair, not content regeneration of full passage. |
| 19 | `book-main:143:0` | body_omitted | 5.2.2 heading only; comparison procedure,0–3 scores,total20,7,35%,25%,5% absent. |
| 20 | `book-main:144:0` | valid_collateral | 5.2.3 title correct;144:1 heading-only latest full response. |
| 21 | `book-main:144:1` | body_omitted | 5.2.3 heading only; ranking table,weights10/100 andratings1–10 absent. Older faithful response also needs Arabicprefix number fix. |
| 22 | `book-main:147:0` | latest_full_response_preserved | Exactsingletonrequest/response fullypreserves relationshipparagraph,brainstorming,mindmapping,nominalgroup,decisionmatrix,Delphi. Currentcheckpointconfirmedaccepted; earlierfailure recovered. |
| 23 | `book-main:149:0` | valid_collateral | 5.3 matrices title complete;149:1 omittedbody. |
| 24 | `book-main:149:1` | body_omitted | 5.3 header only; matrixdefinition andGantt/paired/weighted/ACH/CrossImpact/indicators examples absent. |
| 25 | `book-main:161:0` | source_numeric_coalescing | Full origins prose; source1921.5 mergesyear+footnote as decimal token. Targetmarker preserves merged token; source-aware representation needed. Other2006 preserved. |
| 26 | `book-main:170:0` | body_omitted | Descriptions header only; matrixdimensions and all per-row pairedvalues absent. |
| 27 | `book-main:171:0` | body_omitted | Back to Figure label only; actor taxonomy,engagementmechanisms,1990/2003/2002/1998/2009 andexamples absent. Source itself ends mid-sentence atpage boundary. |
| 28 | `book-main:181:1` | latest_full_response_preserved | Exacttwo-IDrequest181:0/181:1bothoutputscomplete; brainstormingdefinition,rangeofperspectives,analyticproduct,writtenresults preserved. Earlierfailure recovered; checkpointconfirmedaccepted. |
| 29 | `book-main:183:0` | body_omitted | ValueAdded heading only; brainstorming goal and four intuitive traps absent. |
| 30 | `book-main:187:0` | body_omitted | Origins label only;1940s,1953,5th,2019 andbibliography absent. |
| 31 | `book-main:193:0` | body_omitted_short | Relationship heading only; short250charbody definingCluster/Structured/Divergent-Convergent absent;600char adaptivefloor wrongly prevents recovery. |
| 32 | `book-main:194:0` | body_omitted_short | Origins heading only;Handbook5th,2019 andtrainingmaterials absent; shortbody still needs automatic recovery. |
| 33 | `book-main:199:0` | valid_numeric_prefix | Full voting method,ratio examples,4/3/2/1 scores and secondround preserved; markers attached Arabic conjunction should validate. |
| 34 | `book-main:200:0` | body_omitted_short | Relationship heading only; considerbrainstorming andNGT selection conditions absent. |
| 35 | `book-main:201:0` | body_omitted | Origins header only;Delbecq/VandeVen,1971pages466–491,Higgins101/2006 andallsourceURLs absent. |
| 36 | `book-main:213:0` | body_omitted_short | Origins header only;Figure6.5,MindTools91 anddesignorate/tutsplus URLs absent; URLs contain identifiers notordinarynumbers. |
| 37 | `book-main:214:0` | valid_collateral | 6.6 mind/conceptmaps title complete;214:1 omittedbody. |
| 38 | `book-main:214:1` | body_omitted | 6.6 header only;elements,connections,mapping approaches andreference4 absent. |
| 39 | `book-main:215:0` | body_omitted | WhenToUseIt heading only;individual/sharedthinking,communication,mindmapquestions andfigureattribution absent. |
| 40 | `book-main:221:0` | body_omitted_short | Relationship heading only;visualizingbrainstorming/CrossImpact andchapters6/7 absent. |
| 41 | `book-main:222:0` | body_omitted | Origins only; constructivistlearning,reference7,Buzan2006 andbothmappingURLs omitted. |
| 42 | `book-main:223:0` | valid_collateral | 6.7 Venn title correct;223:1 numericmarkers invalid. |
| 43 | `book-main:223:1` | numeric_identifier_fragmentation | Venn body including invaliddog/cat andvalidtiger arguments complete. SourceFigure6.7a tokenized only6 leaving.7a; model raw6.7ب and shiftedmarkers cause rejection. Protect entire numeric identifier. |
| 44 | `book-main:235:0` | joined_source_numbers | Networkmethod complete but sourceSeptember11 andearly2002/modified2008 joined words are missed bynumberlexer. Target separates plainnumbers and partlymisassigns marker after reorderedArabicdate; improve extraction/protection notaccept wrongdates. |
| 45 | `book-main:240:0` | body_omitted | Origins label only; softwarehistory,Krebs,Hanneman,Peterson,Storer/Farrelly,URLs and2019 caption absent. |
| 46 | `book-main:242:0` | body_omitted | Descriptions title only; Who/What/How/Where/When/Why andmindmapdomainitems absent. |
| 47 | `book-main:250:0` | body_omitted | Overview heading only; keyassumptions,chronologies,crossimpact andmultiplehypotheses definitions absent; sourceends mid-sentence atpage boundary. |
| 48 | `book-main:256:0` | body_omitted | ValueAdded only; assumptionsbenefits andanchoring/prematureclosure/availability/overinterpretation examples absent. |
| 49 | `book-main:258:0` | body_omitted | Method only; outsiderreview,facilitator,indexcards,whiteboard,questioning assumptions omitted. |
| 50 | `book-main:276:0` | joined_source_number_false_reject | Full crossimpactmethod rendered. SourceVariable1increase/Event1increase excluded dueglued suffix; Arabicraw1 faithful butappears extra againstmarkersfor2/3/4. Remainingrelations1/2,Figure7.3 anddiscussion preserved. |
| 51 | `book-main:280:0` | body_omitted | 7.4 heading only; hypothesisdefinition,STOPcriteria,MECE andalternativegeneration absent. |
| 52 | `book-main:285:0` | valid_collateral | 7.4.1 simplehypotheses title complete;285:1 body omitted. |
| 53 | `book-main:285:1` | body_omitted | 7.4.1 title only;1–4uses,diversegroup,threealternatives,theory/history/situation andgroupaffinities omitted. |
| 54 | `book-main:287:0` | valid_collateral | 7.4.2 quadranttitle correct;287:1 body omitted. |
| 55 | `book-main:287:1` | body_omitted | 7.4.2 header only;twoforces,fourquadrants,2x2,indicators andIraq scenario absent. |
| 56 | `book-main:289:0` | valid_collateral | 7.4.3 generator title complete;289:1 body omitted. |
| 57 | `book-main:289:1` | body_omitted | 7.4.3 title only; alternativeexplanations,outliers,Who/What/Why,permutations method omitted. |
| 58 | `book-main:290:0` | body_omitted | Figure7.4.3 caption only;2019,discardnonsense,credibility1–5,re-sort/restatement andselection absent. |
| 59 | `book-main:301:0` | body_omitted_short | Relationship label only;diagnosticreasoning/ACH/Indicatorschapter9 andcriticalreasoning body absent. |
| 60 | `book-main:302:0` | body_omitted_short | Origins label only;Elstein/Croskerry,August11/2009,Medicine84/8 andbibliography omitted. |
| 61 | `book-main:320:0` | body_omitted | Origins label only;Heuer1980s,1999reference6,2004/2005software,PaloAlto,Te@mACH andCIAURL absent. |
| 62 | `book-main:321:0` | valid_collateral | 7.7 inconsistencyfinder title complete;321:1 body omitted. |
| 63 | `book-main:321:1` | body_omitted_short | 7.7 title only;shortbody explains disconfirminginformation andsimplerACH absent;600charfloor prevents recovery. |
| 64 | `book-main:327:0` | valid_collateral | 7.8 deceptiontitle complete;327:1 body omitted. |
| 65 | `book-main:327:1` | body_omitted | 7.8 heading only; adversaryinfluence,checklists,Heuer paradox andreference7 absent. |
| 66 | `book-main:329:0` | body_omitted | ValueAdded only; cognitiveburden,Jervis2000quote,deceiverstrategy,Satisficing andconfidence paragraphs absent. |
| 67 | `book-main:338:0` | historical_response_scope_unknown | 537char EVEquestions versus18char Evaluationheading. Cached envelope likelyco-batched, originalrequest notcryptographicallyrecovered; cannotprovefullrequest so do notlabelproviderbodyomissioncertain. Currentcheckpointcoverage stillrequiresfullquestions. |
| 68 | `book-main:341:0` | body_omitted | ValueAdded only; pro/conrelationships,assumptions,gaps,groupthink,causality,base-rates andsmall-sample paragraphs absent. |
| 69 | `book-main:345:0` | body_omitted | Pitfalls label only;practice,experiencedhelper,software/stickynotes method omitted. |
| 70 | `book-main:348:0` | body_omitted | Descriptions only; February–April timeline dates andcrossimpactvariabletable absent. |
| 71 | `book-main:350:0` | valid_numeric_prefix | Full quadrantH1–H4,matrixcontrols andNorthKorea/Pakistan1997/2006/2009 argument preserved; Arabicconjunction prefix before finalmarker valid. |
| 72 | `book-main:352:0` | full_response_preserved | Allchapter8 contentsentries/pages andthreeparagraphs including2008/2016/2 and33markers intact; historicalfailure mayearlierresponse, confirm currentcheckpoint rather thanresubmit blindly. |
| 73 | `book-main:353:0` | source_joined_date_and_semantic_issue | Full Twain/reframing/neuronbody to samepageboundary. Source2002National gluing excludes2002; targetplain2002 extra. September11,2001 translation losesmonth and turnsmarkersinto conjunction dates; genuine datefidelity also needsregeneration of affectedpart. DevilsAdvocacy literalArabicterminology inappropriate. |
| 74 | `book-main:365:0` | body_omitted | ValueAdded only; broadview,outsideforces,incrementalchange andFigure8.1.1b omitted. |
| 75 | `book-main:367:0` | body_omitted | Relationship only;STEEP/PEST/PMESII expansion andreference4 absent. |
| 76 | `book-main:371:0` | body_omitted | ValueAdded only; analogyforecast benefits,32/46/60percentstudy andbiases absent. |
| 77 | `book-main:372:0` | body_omitted | Method only;trainingURL,experts,analogycollection,MIT85conflicts,Maryland452/1918–2006 andcategories absent. |
| 78 | `book-main:373:0` | full_response_preserved | Completecontinuation withanalogywriteups,0–10scale,relevance,forecast,policy andindicatorqueries;4markers correct. |
| 79 | `book-main:374:0` | body_omitted | Pitfalls only; dissimilarconditions,rootcauses,May1975quote andrigorousanalogycritique absent. |
| 80 | `book-main:376:1` | source_footnote_tokenization | FullRedHat/MirrorImaging/culture/Heuer2007quote rendered. SourceAnalysis6 attachedfootnote omittedbyregex,targetArabic6 recognizedextra. |
| 81 | `book-main:385:0` | valid_collateral | 8.2 challengetitle complete;385:1 genuine tailomission. |
| 82 | `book-main:385:1` | tail_omitted | Firsttwo challengeparagraphs rendered; endsatHayden quotedstatement opening, restquote,sevenoutoften,reference10 andprobabilityparagraph missing. |
| 83 | `book-main:392:0` | numeric_identifier_and_joined_suffix | Fullattackassumptionmethod rendered. PartialFigure8.2.1.1a tokenprotection leaves.1letters;2-x-2matrices sourcegluedsecond2 excluded,targetraw-2 addsnumeric. Need exactidentifier/numberprotection. |
| 84 | `book-main:393:0` | full_response_identifier_artifact | Allattackselectioncriteria,plans1/4/7/16/nightmare11 andnext8.2.1.2heading rendered; numericfigurefragment .1d requiresconsistentlexer/captionprotection, notblanketbodyregen. |
| 85 | `book-main:394:0` | tail_omitted | OnlyintroductoryForesightQuadrantparagraph rendered; all10methodsteps absent althoughoutputoverheading. Numbermarkers inlatersteps missing; automatic subdivision mustcoverlist. |
| 86 | `book-main:400:0` | body_omitted | ValueAdded only; premortemcreativity,reframing,dissent,groupthink andreference14 absent. |
| 87 | `book-main:405:0` | body_omitted | Origins only;GaryKlein1998/2002/2007,AtlassianURL,figure8.2.2 and2019 absent. |
| 88 | `book-main:409:0` | body_omitted | Method only; blackhatrole,sourcesuncertainty1–3,puzzle/mystery,reference16 andevidencequestions absent. |
| 89 | `book-main:413:0` | body_omitted | Pitfalls only; advocate-to-critictransition,dedicatedmeeting,changedleader/location andfacilitation absent. |
| 90 | `book-main:424:0` | body_omitted | Origins only;Handbook5th2019 andsensitive-scenario cautionarynote fullyabsent. |
| 91 | `book-main:428:0` | body_omitted | ValueAdded only; consequenceswarning,crediblepaths andmirror/anchoring/groupthink/informationabsence biases omitted. |
| 92 | `book-main:431:0` | body_omitted | Pitfalls only;1–25/1–10percent interpretation,reference19,5/10–20 ranges andArcticexample omitted. |
| 93 | `book-main:432:0` | body_omitted | Relationship only;WhatIf backward reconstruction versusHighImpact forwardwarning differences absent. |
| 94 | `book-main:433:0` | source_footnote_tokenization | WholeFigure8.2.5caption andattribution preserved; sourceArctic20 glued whiletargetArabic20 recognizedextra. |
| 95 | `book-main:435:0` | valid_collateral | 8.2.6 Delphititle complete;435:1 omittedbody. |
| 96 | `book-main:435:1` | body_omitted | Delphititle only; experts,two-or-more rounds,anonymity,clarification andflexibility absent. |
| 97 | `book-main:442:0` | body_omitted | Pitfalls only;Delphiadministration,clearances,approvedspaces andforcedconsensus risk absent. |
| 98 | `book-main:460:0` | body_omitted | ValueAdded only; writtenopposingcases,refutation,scientificmethodchapter7,Mill1859quote andconstructivedebate absent. |
| 99 | `book-main:463:0` | body_omitted_short | Relationship only;TeamA/Bdifference,emotionreduction,thirdeditionchanges andfootnote32 absent. |
| 100 | `book-main:464:0` | body_omitted_short | Origins only;Socraticdebate andHeuersrefutationfocus absent. |
| 101 | `book-main:469:0` | body_omitted | Descriptionheading only;skiing/cognitiveframe,office/worldinfluence,geometricanalogies andquadrants1/2 absent. |
| 102 | `book-main:471:0` | valid_numeric_prefix | FullDelphiflow preserved withsevensteps,1/3/5facilitator versus2/4/6experts,7report andfeedback; conjunction-attachedmarkers valid. |
| 103 | `book-main:490:0` | body_omitted | ValueAdded only;uncertaintyfinder,keyassumptions/drivergeneration andcriticalvariabletrajectory absent. |
| 104 | `book-main:491:0` | body_omitted | Method only; StageIassumptions,StageIIuncertainties andStageIIIdrivers absent. |
| 105 | `book-main:498:0` | numeric_identifier_fragmentation_faithful_target | Fullsimplescenariosmethod andsixdrivers preserved. Exactoriginalrequestbinding: sourceFigure9.4 hasunprotected9.andmarker2=4, so target9.marker2 iscorrect9.4, notadded9. Domainrevalidationaccepts. Lexerprotectfullfigureid infuture; preservefaithfulcheckpoint. |
| 106 | `book-main:505:0` | body_omitted_short | Origins only;ConeofPlausibilityCanada/UK andprescient2050URL omitted. |
| 107 | `book-main:508:0` | body_omitted | Method only;AlternativeFuturesCuba/twoforces/2x2/narratives/indicators,Figure9.6/2019 absent. |
| 108 | `book-main:514:0` | body_omitted | Method only; multiple-scenarios combinations,training,steps,Iraqforces andFigure9.7a absent. |
| 109 | `book-main:515:0` | joined_source_numbers_and_matrix_sign | CompleteIraq scenariosbody rendered; sourceeach2-×-2 gluing excludesfirst2,targetraw2 extra; minus consumedasmatrixnumbersign,Figure9.7suffixpartial. Needexactsourceawarepreservation. |
| 110 | `book-main:526:0` | body_omitted | ValueAdded only; counterfactualclaims,causes/context/consequences,creativity andhindsightbias safeguards absent. |
| 111 | `book-main:527:0` | body_omitted | Method only; threestages1/2/3questions andFigure9.9 absent. |
| 112 | `book-main:532:0` | body_omitted | MultilineStageThreeheading only; Stage3with1/2interactions,selection,andconditionalclaim warnings absent. |
| 113 | `book-main:533:0` | valid_numeric_prefix | Fullcounterfactualrelationships toassumptions,WhatIf,RedHat,HighImpact/foresight andstages1/2 preserved; conjunctionprefixmarkers valid. |
| 114 | `book-main:534:0` | body_omitted | Origins only;Aristotle/Plato/Leibniz,Kahneman/Tversky,Hendrickson andreferences10/11/12 absent. |
| 115 | `book-main:547:0` | body_omitted | ValueAdded only;indicatorcriteria,Figure9.11/1990/2003,learning,disagreement1–3 anddualindicatorlists absent. |
| 116 | `book-main:549:0` | valid_collateral | 9.11.1 indicatorgenerationtitle correct;549:1 body omitted. |
| 117 | `book-main:549:1` | body_omitted | 9.11.1 header only; STEMPLES+,alltechniqueexamples,reference16,normality andsetscriteria omitted. |
| 118 | `book-main:555:0` | numeric_identifier_fragmentation_and_semantic_issue | Bodythroughsamepageboundary complete. Exactoriginalrequestbinding: joinedFigure9.11.3c protects11asmarker3, leaving9.and.3c; target9.marker3.3ج faithfullypreserves9.11.3, notadded9. Domaingateaccepts. Opening HighlyUnlikelyis5points rating mistranslatedasprobabilityclaim; glossary/categorystructure needed. |
| 119 | `book-main:561:0` | body_omitted | NOTES label only; sevenbibliographyentries,numerousyears/pages/URLs andgovernmentmanuals absent. |
| 120 | `book-main:564:0` | complete_with_format_and_terms_issues | Allfiguredescriptions/drivercases andyears/ranges preserved, butblockseparators collapsedandForesightQuadrantCrunching phrase fragmented semantically. Automaticstructure+terminologyconsistency needed. |
| 121 | `book-main:565:0` | introduced_markup_and_joined_year | Fullthree-stage/narrative/tablecaption mostlypreserved butsourceand2009 excludedbyregex,targetraw2009extra andhallucinatedHTMLcitation[book-main:p565] introducespage565 absentfromsource; rejectmarkup/provenance andrepaironlyaffectedunit. |
| 122 | `book-main:566:0` | table_cell_duplication | Ratingtablebodytranslated but OrganizationalCapabilities row sourceLow+5Negligible vs targetLow+6Negligible; wrongcellcount despitecorrect2008/2009. Alltablecolumns/rowsneedexactcellprotection, readable formatting. |
| 123 | `book-main:567:0` | table_structure_preserved_formatting_poor | Quartertable ninecategorizedrows each6values faithful includingthreatconflict3Low/3Negligible andsecurity4Negligible/2Low. Outputhasnogoodrowseparators; structuralformatterneeded. |
| 124 | `book-main:585:0` | body_omitted | ValueAdded only;Bowtierisk/control/consequences/graphics andcognitivebiases absent. |
| 125 | `book-main:589:0` | body_omitted | Relationship only;Bowtie/DecisionTrees/barriers/OpportunityIncubator/drivers absent. |
| 126 | `book-main:591:0` | valid_collateral | 10.3 impacttitle correct;591:1 body omitted. |
| 127 | `book-main:591:1` | body_omitted_short | 10.3 heading only; actorinterest/impact,managerstrategies andforeignleaderanticipation absent. |
| 128 | `book-main:594:0` | tail_omitted | Impactmatrixfirstparagraph andpartofidentifyactorsstep rendered; endsatopeningquote beforeme=decisionmaker. Allrating/impact/review/actionsteps andsecondFigure10.3 absent. |
| 129 | `book-main:597:0` | valid_collateral | 10.4 SWOTtitle correct;597:1 body omitted. |
| 130 | `book-main:597:1` | body_omitted_short | 10.4 heading only;Strengths/Weaknessesinternal,Opportunities/Threatsexternal andcompetitiveanalysis omitted. |
| 131 | `book-main:599:0` | body_omitted | ValueAdded only;SWOTpracticalframework andconfirmation/anchoring/groupthink biases absent. |
| 132 | `book-main:601:0` | body_omitted | Pitfalls only;single-goallimitation,alternativeactions,TOWSMatrix andreference2 absent. |
| 133 | `book-main:608:0` | valid_collateral | 10.6 DecisionTreestitle correct;608:1 body omitted. |
| 134 | `book-main:608:1` | body_omitted_short | 10.6 heading only; decisionchains/options/probability/outcomes explanation absent. |
| 135 | `book-main:610:0` | body_omitted | ValueAdded only;DecisionTreecreation/groupbrainstormingchapter6,updating andbiases absent. |
| 136 | `book-main:619:0` | joined_source_number_false_reject | FullDecisionMatrixmethod rendered,source to100 merged excludessecond100,targetcorrectraw100extra; Figure10.7 andfirst100 marker intact. Preservefaithfulbody,fix sourcelexer. |
| 137 | `book-main:621:0` | body_omitted_short | Origins only;MAUA/MCDA/MCDM/Pugh/Utilitynames andParlos/Triantaphyllou2000citation absent. |
| 138 | `book-main:624:0` | body_omitted | ValueAdded only;ForceFielddriving/restraining,Figure10.8,Lewin1948,2019 andbiases absent. |
| 139 | `book-main:629:0` | body_omitted_short | 10.9 title only; policycritique/preventprematureconclusion body omitted. |
| 140 | `book-main:631:0` | body_omitted | ValueAdded only;positive/negativebias,Pros/Cons/Faults/Fixesdefinition andbalancedjudgment absent. |
| 141 | `book-main:635:0` | body_omitted | Pitfalls only;weightedscores/manipulationandavoidmechanicalsum advice absent. |
| 142 | `book-main:636:0` | body_omitted | Origins only;Heuer/Jones1998pages72–79andnegativity/overconfidence contrast omitted. |
| 143 | `book-main:640:0` | body_omitted | ValueAdded only;complexsystems/interactions,simulationbenefits,references5/6 andquantifiabledynamics absent. |
| 144 | `book-main:642:0` | body_omitted | Method only;ComplexityManagersteps1–4 andvariables20/10/90/15/210 plusmatrix/signs description absent. |
| 145 | `book-main:643:0` | joined_source_number_false_reject | Fullsteps5/6andprecedingratings/bodycomplete; source3indicating excluded,targetcorrectraw3 extra. Pageendsmid-sentence inboth. Other9markers preserved. |
| 146 | `book-main:644:0` | full_response_preserved | Completecontinuation andsteps7/8,referenced5/6/chapter6;5markers preserved. Source report.8 ispage/PDFjoinedlistboundary,not modelomission. |
| 147 | `book-main:646:0` | body_omitted_short | Origins only;HeuerComplexityManager,simplifiedquantitative/SystemDynamics body absent. |
| 148 | `book-main:649:0` | table_body_omitted | A–I header only;rowA–Flabels andallpositive/negative/Nilimpactcells absent. Sourcecontainsclippednegativ,requiresliteralpreservation notinventedvalue. |
| 149 | `book-main:653:0` | valid_collateral | 11.1 empiricaltitle correct;653:1 body omitted. |
| 150 | `book-main:653:1` | body_omitted | 11.1 header only;RAND2016,Coulthart2015,Chang2018andempiricallimitations/references1–3 absent. |
| 151 | `book-main:665:1` | source_footnote_tokenization | Allkeydrivers/collaboration/generation/support/research paragraphs preserved; source techniques12 glued,targetArabic12 recognizedextra. Analytictradecraft term awkward requiresglossaryqualityflag. |
| 152 | `book-main:677:0` | short_table_omission_undetected | 145charsourceFbudgetrowandratings vs27chartargetlastStrongpositiveandstrongnegative only. Below200char floor hidesgenuineomission; categoricalcellcoverage mandatory. |
| 153 | `book-main:677:1` | table_body_omitted | OnlyrowGlabel translated;G/H/Iimpactvaluesandlabels omitted. SourceMn/Mpamn uncertainOCRcodes preserveexactlywithwarning,notinventcellmeaning. |
| 154 | `book-main:677:2` | short_table_omission_undetected | ~105charsourceJlabel plus3MediumnegativeandMpamn; targetonlyJlabel~50chars wouldpasslengthfloor. Cellsequencepreservation required. |
