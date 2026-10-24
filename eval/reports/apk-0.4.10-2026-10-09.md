# APK0.4.10 readiness —2026-10-09

Implemented editable/persistent validated HTTPS origin and same explicit trusted-host443/8443 health failover. JSON GET may retry once on transport/502/503/504 failures; mutations never blindly retry an ambiguous response. Redirects are disabled on authenticated JSON/upload/source retrieval. Samehost endpoint changes preserve token and progress; explicit crosshost selection requires new token confirmation. Null connection-label access is guarded.

Translation UI understands terminal partial outcomes and actual saved/total coverage. It can resume gaps from the exact server resume request. Generic stage0/1/4 resume uses GET capability checks and POST with selectedbook_id; QA stage actions use dedicated backend routes. Saved OCR/RAG/questions/audio preview actions are shown on their stage cards. Unsupported resume capabilities stay hidden.

Validation:
- Plain Java EndpointRouting regression: PASS; validates HTTPS, port normalization, fixedhost alternatives, customhost isolation and unavailable routes.
- Android8/API27 x86_64 instrumentation: PASS; mutation transport failure produces one request only; GET fallback produces two requests; persistentendpoint; capabilitygating; terminalpartial60%coverage andresume; fullcompleted noresume; nullableconnectionlabel; existing resume/catalogregressions.
- ARMv7 assembleDebug: PASS; minSDK24/versionCode16/versionName0.4.10; onlylib/armeabi-v7a nativeentry; apksigner verificationPASS.
- SHA25669394289201fd8ef7a70d85c18d6c029722ae8110bce588fdaf976b14fe30743.
- Firstfive-tabsmoke attempt was obstructed by coldboot Android SystemUI ANR, not the Book2Course process. SystemUI recovered; five-tab navigation, install/launch and background/resume retry PASS with no AndroidRuntime crash. Retry evidence is stored under eval/android/2026-10-09-v0410-api27-retry.

Deliverable preserved at outputs/Book2Course-operator-v0.4.10-armv7-2026-10-09.apk. No older APK removed. No server rebuild/restart, workflow creation, paid inference or Drive upload by this agent. The parent coordinates compatible API rollout/upload.
