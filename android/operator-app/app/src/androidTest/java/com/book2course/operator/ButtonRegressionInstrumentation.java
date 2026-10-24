package com.book2course.operator;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import org.json.JSONObject;
public class ButtonRegressionInstrumentation extends Instrumentation {
 static class OfflineRequests extends MainActivity {
  int calls=0,selections=0;String firstMethod="";
  @Override String ensureApiToken(){return "offline-test-only";}
  @Override synchronized String readyBase(boolean force,String avoid){selections++;return "https://"+EndpointRouting.TRUSTED_HOST+(avoid==null?"":":8443");}
  @Override JSONObject requestOnce(String origin,String method,String path,JSONObject body,String token)throws Exception{calls++;firstMethod=method;if(calls==1)throw new java.io.IOException("offline transport");return new JSONObject().put("ok",true);}
 }
 static class OfflineTranslationRequests extends MainActivity {
  JSONObject response;int creates=0,resumes=0;JSONObject payload;
  @Override JSONObject get(String path){return response;}
  @Override JSONObject req(String method,String path,JSONObject body)throws Exception{resumes++;payload=body;return new JSONObject().put("workflow_id","resumed");}
  @Override String startTranslation(JSONObject source,String kind,String target){creates++;return "created";}
 }
 static class OfflinePauseRequests extends MainActivity {
  String requested="";final java.util.concurrent.CountDownLatch called=new java.util.concurrent.CountDownLatch(1);
  @Override JSONObject req(String method,String path,JSONObject body){requested=path;called.countDown();return new JSONObject();}
  @Override void showActionMessage(String heading,String message){}
 }
 static class OfflineTranslationStartRequests extends MainActivity {
  JSONObject payload;String requested="";
  @Override JSONObject req(String method,String path,JSONObject body)throws Exception{payload=body;requested=path;return new JSONObject().put("workflow_id","new-translation");}
 }
 public void onCreate(Bundle arguments){super.onCreate(arguments);start();}
 public void onStart(){Bundle result=new Bundle();try{
  OfflineRequests[] doubles=new OfflineRequests[2];runOnMainSync(()->{doubles[0]=new OfflineRequests();doubles[1]=new OfflineRequests();});OfflineRequests post=doubles[0];try{post.req("POST","/v1/test",new JSONObject());throw new AssertionError("Mutation failure must surface");}catch(java.io.IOException expected){}if(post.calls!=1||post.selections!=1)throw new AssertionError("Ambiguous mutation retried");
  OfflineRequests get=doubles[1];if(!get.req("GET","/v1/test",null).optBoolean("ok")||get.calls!=2||get.selections!=2)throw new AssertionError("Safe GET fallback unavailable");
  OfflineTranslationRequests[] translationDoubles=new OfflineTranslationRequests[1];runOnMainSync(()->translationDoubles[0]=new OfflineTranslationRequests());OfflineTranslationRequests translations=translationDoubles[0];translations.p=getTargetContext().getSharedPreferences("b2c",0);
  translations.response=new JSONObject().put("status","COMPLETED");
  if(!"completed".equals(translations.ensureTranslationWorkflow(new JSONObject(),"book","ar","completed","course"))||translations.creates!=0||translations.resumes!=0)throw new AssertionError("Saved complete translation must be reused");
  translations.response=new JSONObject().put("status","FAILED").put("resume_request",new JSONObject().put("source_kind","book"));
  if(!"resumed".equals(translations.ensureTranslationWorkflow(new JSONObject(),"book","ar","failed","course"))||translations.creates!=0||translations.resumes!=1||translations.payload.optBoolean("force_regenerate",true)||!"failed".equals(translations.payload.optString("resume_from_workflow_id")))throw new AssertionError("Failed translation must resume checkpoints");
  OfflinePauseRequests[] pauses=new OfflinePauseRequests[1];runOnMainSync(()->pauses[0]=new OfflinePauseRequests());pauses[0].qa="original-qa";pauses[0].pipelineRunning=true;pauses[0].pauseQA();pauses[0].qa="different-qa";
  if(!pauses[0].called.await(5,java.util.concurrent.TimeUnit.SECONDS)||!"/v1/course-qa/original-qa/pause".equals(pauses[0].requested))throw new AssertionError("Pause must target captured workflow and stay usable during pipeline");pauses[0].monitorIo.shutdownNow();
  MainActivity activity=(MainActivity)startActivitySync(new Intent().setClassName(getTargetContext().getPackageName(),MainActivity.class.getName()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
  runOnMainSync(()->{try{
   String[] savedRefs={activity.ingest,activity.index,activity.qa,activity.translation,activity.bookTranslation,activity.audio,activity.approved};
   try{
    activity.audioEntries.add(new JSONObject().put("track_id","another-book"));activity.activeTrack=0;
    activity.index="old-index";activity.qa="old-qa";activity.translation="old-translation";activity.bookTranslation="old-body";activity.audio="old-audio";activity.approved="old-outline";
    activity.applyStageRegistry(new JSONObject().put("ingest",new JSONObject().put("workflow_id","new-ingest")));
    if(!"new-ingest".equals(activity.ingest)||!activity.index.isEmpty()||!activity.qa.isEmpty()||!activity.translation.isEmpty()||!activity.bookTranslation.isEmpty()||!activity.audio.isEmpty()||!activity.approved.isEmpty())throw new AssertionError("New-book registry retained another book stage");
    if(!activity.audioEntries.isEmpty()||activity.activeTrack!=-1)throw new AssertionError("Another book audio selection retained");
    activity.applyStageRegistry(new JSONObject().put("ingest",new JSONObject().put("workflow_id","old-ingest")).put("qa",new JSONObject().put("workflow_id","old-qa")).put("book_translation",new JSONObject().put("workflow_id","old-body")));
    if(!"old-qa".equals(activity.qa)||!"old-body".equals(activity.bookTranslation)||!activity.audio.isEmpty())throw new AssertionError("Saved book refs not restored independently");
    for(String action:new String[]{"run","reuse"})if(!activity.planNeedsTranslation(new JSONObject().put("stages",new org.json.JSONArray().put(new JSONObject().put("stage","translation").put("action",action)))))throw new AssertionError("Required translated input ignored: "+action);
    if(activity.planNeedsTranslation(new JSONObject().put("stages",new org.json.JSONArray().put(new JSONObject().put("stage","translation").put("action","skip")))))throw new AssertionError("Optional translation unexpectedly enabled");
   }finally{activity.ingest=savedRefs[0];activity.index=savedRefs[1];activity.qa=savedRefs[2];activity.translation=savedRefs[3];activity.bookTranslation=savedRefs[4];activity.audio=savedRefs[5];activity.approved=savedRefs[6];}
   for(int stage=0;stage<5;stage++)if(activity.stageStartButtons[stage]==null||activity.stageStartButtons[stage].getVisibility()!=android.view.View.VISIBLE||!activity.stageStartButtons[stage].getText().toString().contains("متابعة"))throw new AssertionError("Missing persistent stage start action: "+stage);
   JSONObject titleA=new JSONObject().put("title","كتاب التجربة").put("source_filename","Analysis Methods.pdf").put("book_id","book-12345678");
   JSONObject titleB=new JSONObject().put("title","Analysis Methods").put("source_filename","Edition 2.pdf").put("book_id","book-87654321");
   if(!"Analysis Methods".equals(activity.bookDisplayTitle(titleA)))throw new AssertionError("Generic title must show filename");
   java.util.ArrayList<JSONObject> names=new java.util.ArrayList<>();names.add(titleA);
   if(!activity.bookSecondaryIdentity(names,titleA).isEmpty())throw new AssertionError("Unique title need not show raw identifier");names.add(titleB);
   if(!activity.bookSecondaryIdentity(names,titleA).contains("12345678"))throw new AssertionError("Duplicate title must remain distinguishable");
   String priorTranslation=activity.translation,priorBody=activity.bookTranslation;
   try{activity.translation="";activity.bookTranslation="body-only";if(!"body-only".equals(activity.mainTranslationWorkflow()))throw new AssertionError("Body-only translation must populate global stage");activity.translation="appendix";if(!"appendix".equals(activity.mainTranslationWorkflow()))throw new AssertionError("Appendix route retained");}finally{activity.translation=priorTranslation;activity.bookTranslation=priorBody;}
   activity.pipelineRunning=false;
   String originalEndpoint=activity.p.getString("api_base_url",null);
   try {
    activity.p.edit().putString("api_base_url","https://"+EndpointRouting.TRUSTED_HOST+":8443/").commit();
    if(!activity.configuredBase().endsWith(":8443"))throw new AssertionError("Persistent endpoint override missing");
    if(EndpointRouting.candidates(activity.configuredBase()).size()!=2)throw new AssertionError("Known port backup missing");
    JSONObject partial=new JSONObject().put("status","COMPLETED").put("result_status","partial")
     .put("progress",new JSONObject().put("completed_segments",6).put("total_segments",10).put("retry_segments",4))
     .put("resume_request",new JSONObject());
    if(!activity.translationCanResume(partial)||activity.translationTrackPercent(partial)!=60)throw new AssertionError("Partial translation must resume and show true coverage");
    activity.updateTranslationRetry(true,partial);
    if(activity.bodyTranslationRetry.getVisibility()!=android.view.View.VISIBLE||!activity.bodyTranslationRetry.isEnabled())throw new AssertionError("Partial translation retry hidden");
    if(activity.translationCanResume(new JSONObject().put("status","COMPLETED")))throw new AssertionError("Complete translation must not duplicate");
    android.widget.TextView previous=activity.connectionStatus;activity.connectionStatus=null;activity.updateConnectionLabel("Offline guard check");activity.connectionStatus=previous;
   }finally{if(originalEndpoint==null)activity.p.edit().remove("api_base_url").commit();else activity.p.edit().putString("api_base_url",originalEndpoint).commit();activity.activeEndpoint="";}
   for(String terminal:new String[]{"FAILED","TIMED_OUT","CANCELED","TERMINATED"})if(!activity.translationTerminal(terminal))throw new AssertionError("Terminal resume state missing:"+terminal);
   if(activity.translationTerminal("RUNNING")||activity.translationTerminal("COMPLETED"))throw new AssertionError("Active/full-complete generic stage must not resume");
   String savedIngest=activity.ingest;try{activity.ingest="b2c-ingest-test";activity.showStageResumeCapability(0,activity.ingest,new JSONObject().put("resume_available",false));if(activity.stageResumeButtons[0].getVisibility()!=android.view.View.GONE)throw new AssertionError("Unsupported resume must be hidden");activity.showStageResumeCapability(0,activity.ingest,new JSONObject().put("resume_available",true));if(activity.stageResumeButtons[0].getVisibility()!=android.view.View.VISIBLE)throw new AssertionError("Backend-supported resume missing");}finally{activity.ingest=savedIngest;activity.stageResumeButtons[0].setVisibility(android.view.View.GONE);}
   activity.updateTranslationRetry(true,new JSONObject().put("status","FAILED"));
   if(!activity.bodyTranslationRetry.isEnabled())throw new AssertionError("Unavailable resume check must stay clickable");
   if(!"فحص إتاحة الاستئناف".contentEquals(activity.bodyTranslationRetry.getText()))throw new AssertionError("Missing explicit resume-check label");
   activity.updateTranslationRetry(true,new JSONObject().put("status","FAILED").put("resume_request",new JSONObject()));
   if(!"استئناف المقاطع غير المحفوظة".contentEquals(activity.bodyTranslationRetry.getText()))throw new AssertionError("Available resume label is wrong");
   activity.pipelineRunning=true;
   activity.updateTranslationRetry(true,new JSONObject().put("status","FAILED").put("resume_request",new JSONObject()));
   if(activity.bodyTranslationRetry.isEnabled())throw new AssertionError("Busy pipeline must block duplicate resume");
   String protectedBook=activity.book.getText().toString(),protectedIngest=activity.ingest,protectedAudio=activity.audio;
   activity.pipelineRunning=true;activity.setPipelineContextLocked(true);
   if(activity.book.isEnabled()||activity.course.isEnabled()||activity.target.isEnabled()||activity.stageStartButtons[0].isEnabled())throw new AssertionError("Processing context remains editable");
   activity.selectBook(new JSONObject().put("book_id","other-book").put("course_id","other-course").put("workflow_id","other-workflow").put("title","Other"));
   activity.applyComposition(new java.util.ArrayList<>(),new java.util.ArrayList<>(),"other-book");
   if(!protectedBook.equals(activity.book.getText().toString())||!protectedIngest.equals(activity.ingest)||!protectedAudio.equals(activity.audio))throw new AssertionError("Busy pipeline changed book/workflow context");
   if(activity.monitorIo==activity.io)throw new AssertionError("Independent book monitor must not wait behind pipeline executor");
   activity.pipelineRunning=false;activity.setPipelineContextLocked(false);
   if(!activity.book.isEnabled()||!activity.course.isEnabled()||!activity.target.isEnabled()||!activity.stageStartButtons[0].isEnabled())throw new AssertionError("Context not unlocked after pipeline");
   String savedProvider=activity.p.getString("translation_provider_id",null);
   try{
    activity.p.edit().remove("translation_provider_id").commit();
    activity.rememberConfirmedModelDefault(new JSONObject().put("id","hy-mt2-1-8b").put("state","starting"));
    if(activity.p.contains("translation_provider_id"))throw new AssertionError("Unconfirmed model default must not override translation");
    JSONObject hy=new JSONObject().put("id","hy-mt2-1-8b").put("label","Hy-MT2 1.8B").put("evaluation_status","limited_test").put("kind","local").put("stages",new org.json.JSONArray().put("translation")).put("default_for_stages",new org.json.JSONArray().put("translation")).put("job_switching_supported",true);
    activity.rememberConfirmedModelDefault(hy);
    JSONObject request=new JSONObject();activity.applyTranslationProviderChoice(request);
    if(!"hy-mt2-1-8b".equals(request.optString("provider_id")))throw new AssertionError("Confirmed HY selection missing from translation payload");
    activity.modelCatalogLoaded=true;activity.modelCatalog=new org.json.JSONArray().put(hy);activity.modelStage.setSelection(5);activity.filterModelOptions();
    if(activity.visibleModels.size()!=1||!"hy-mt2-1-8b".equals(activity.selectedModel().optString("id")))throw new AssertionError("Connected tested HY not selectable in translation settings");
    OfflineTranslationStartRequests start=new OfflineTranslationStartRequests();start.p=activity.p;start.course=activity.course;start.lang=activity.lang;start.tokens=activity.tokens;start.detectedSourceLanguage="en";
    start.startTranslation(new JSONObject().put("key","saved-source"),"book","ar");
    if(!"hy-mt2-1-8b".equals(start.payload.optString("provider_id"))||!"saved-source".equals(start.payload.getJSONObject("source_artifact_ref").optString("key")))throw new AssertionError("New HY translation request changes source or drops provider");
    OfflineTranslationRequests resume=new OfflineTranslationRequests();resume.p=activity.p;resume.response=new JSONObject().put("status","FAILED").put("resume_request",new JSONObject().put("source_artifact_ref",new JSONObject().put("key","saved-source")));
    resume.ensureTranslationWorkflow(new JSONObject(),"book","ar","prior-translation","course");
    if(!"hy-mt2-1-8b".equals(resume.payload.optString("provider_id"))||resume.payload.optBoolean("force_regenerate",true)||!"prior-translation".equals(resume.payload.optString("resume_from_workflow_id")))throw new AssertionError("HY resume must preserve checkpoints");
    activity.pipelineRunning=true;activity.updateQAStageActions(new JSONObject().put("status","RUNNING"));if(activity.qaStagePause.getVisibility()!=android.view.View.VISIBLE||!activity.qaStagePause.isEnabled())throw new AssertionError("Supported QA pause unavailable while pipeline running");activity.pipelineRunning=false;activity.updateQAStageActions(new JSONObject().put("status","PAUSED"));if(activity.qaStageResume.getVisibility()!=android.view.View.VISIBLE)throw new AssertionError("Paused QA resume control missing");
   }finally{activity.pipelineRunning=false;if(savedProvider==null)activity.p.edit().remove("translation_provider_id").commit();else activity.p.edit().putString("translation_provider_id",savedProvider).commit();}
   activity.modelCatalogLoaded=false;activity.modelCatalog=new org.json.JSONArray();activity.filterModelOptions();
   if(!activity.modelChoice.getSelectedItem().toString().contains("تحديث النماذج"))throw new AssertionError("Unloaded catalogue must not claim no tested models");
   activity.modelCatalogLoaded=true;activity.modelCatalog=new org.json.JSONArray().put(new JSONObject().put("id","old-model").put("stages",new org.json.JSONArray().put("ocr")));activity.filterModelOptions();
   if(!activity.modelChoice.getSelectedItem().toString().contains("الخادم"))throw new AssertionError("Old catalogue must explain incompatible metadata");
   activity.modelCatalog=new org.json.JSONArray().put(new JSONObject().put("id","tested").put("label","Tested OCR").put("evaluation_status","tested").put("stages",new org.json.JSONArray().put("ocr"))).put(new JSONObject().put("id","untried").put("label","Untried OCR").put("evaluation_status","not_tested").put("stages",new org.json.JSONArray().put("ocr")));
   activity.modelStage.setSelection(0);activity.filterModelOptions();if(activity.visibleModels.size()!=1||!"tested".equals(activity.visibleModels.get(0).optString("id")))throw new AssertionError("Catalogue must show tested options only");
  }catch(Exception e){throw new RuntimeException(e);}});
  result.putString("result","PASS: confirmed tested HY selectable and passed on new/resumed checkpoint translation requests; backend-supported QA pause/resume only; pause targets immutable captured workflow during pipeline; busy pipeline book/workflow context locked, independent monitor executor, unlock after pipeline; complete translation reuse and failed checkpoint resume; persistent action on all five stages; natural titles and duplicate identity; body-only translation visible; book stage refs independently replace old cache and clear stale audio selection; translation run/reuse required and skip optional; mutation never replayed; safe GET falls back once; capability-based stage controls; HTTPS endpoint persistence; known-host port routing; partial translation true coverage/resume; completed translation no duplicate; null status label guarded; unavailable resume check enabled; ready resume label; duplicate resume blocked while pipeline active; unloaded and legacy catalogue explained; tested-only filter; no HTTP/inference requests");
  runOnMainSync(activity::finish);finish(0,result);
 }catch(Throwable e){result.putString("failure",e.toString());finish(1,result);}}
}
