package com.book2course.operator;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import org.json.*;

/** Offline fixture checks: no server mutation, credentials or inference. */
public class OperatorNavigationInstrumentation extends Instrumentation {
 public void onCreate(Bundle args){super.onCreate(args);start();}
 static boolean contains(View root,View wanted){if(root==wanted)return true;if(root instanceof ViewGroup){ViewGroup group=(ViewGroup)root;for(int i=0;i<group.getChildCount();i++)if(contains(group.getChildAt(i),wanted))return true;}return false;}
 public void onStart(){Bundle report=new Bundle();MainActivity app=null;try{
  getTargetContext().getSharedPreferences("b2c",0).edit().clear().commit();
  Intent intent=new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);app=(MainActivity)startActivitySync(intent);final MainActivity a=app;
  runOnMainSync(()->{try{
   if(a.pages.length!=5||!"إعداد المراحل".contentEquals(a.navButtons[1].getText())||!"المتابعة".contentEquals(a.navButtons[2].getText()))throw new AssertionError("Five agreed operator tabs missing");
   if(!contains(a.pages[1],a.lq)||contains(a.pages[2],a.lq)||!contains(a.pages[1],a.enrichmentToggle))throw new AssertionError("Setup controls must be separate from progress");
   if(!contains(a.pages[3],a.audioSelection)||!contains(a.pages[3],a.resultStage))throw new AssertionError("Outputs must expose audio and results together");
   if(!contains(a.pages[1],a.stageModelButtons[5]))throw new AssertionError("Translation stage model choice missing");
   a.summaryToggle.setChecked(true);a.enrichmentToggle.setChecked(false);a.assessmentToggle.setChecked(true);a.p.edit().putString("review_assessment_scopes","[\"final\"]").commit();JSONObject options=new JSONObject();a.applyQAOptions(options);if(!options.getBoolean("include_lesson_summaries")||options.getBoolean("include_lesson_enrichment")||options.getJSONArray("review_assessment_scopes").length()!=1||!"final".equals(options.getJSONArray("review_assessment_scopes").getString(0)))throw new AssertionError("Independent summary/enrichment/scope configuration lost");a.assessmentToggle.setChecked(false);a.applyQAOptions(options);if(options.getJSONArray("review_assessment_scopes").length()!=0)throw new AssertionError("Disabled assessments must send explicit empty scopes");
   if(!"/v1/courses/course-a/results/translate-content%3Aabc".equals(a.resultRoute("course-a","translate-content:abc")))throw new AssertionError("Saved result identity must be encoded in course-scoped route");
   String preview=a.savedResultPreview(new JSONObject().put("content",new JSONObject().put("title","Lesson title").put("text","Original lesson body").put("items",new JSONArray().put(new JSONObject().put("question","What is the concept?").put("answer","An explanation").put("evidence_quote","Quoted source")))).toString());if(!preview.contains("Original lesson body")||!preview.contains("What is the concept?")||!preview.contains("An explanation")||!preview.contains("Quoted source")||preview.startsWith("{"))throw new AssertionError("Saved JSON preview must present readable lesson/questions/answers/evidence");
   if(!"/v1/courses/course+a/memberships/book%3A1/materialize".equals(a.materializePath("course a","book:1")))throw new AssertionError("Materialization must encode target-course and book identity");
   a.course.setText("revolution");a.audio="live-processing-workflow";
   JSONObject track=new JSONObject().put("track_id","lesson-20").put("display_filename","Revolution20.mp3").put("download_url","/v1/audio/saved/tracks/lesson-20");
   JSONObject run=new JSONObject().put("workflow_id","historical-saved-workflow").put("status","COMPLETED").put("zip_url","/v1/courses/revolution/audio/zip?workflow_id=saved").put("tracks",new JSONArray().put(track));
   a.applyAudioFolder("revolution",new JSONObject().put("course_name","Revolution"),run);
   if(!"live-processing-workflow".equals(a.audio)||!"historical-saved-workflow".equals(a.audioBrowseWorkflow))throw new AssertionError("Browsing audio must not overwrite active generation workflow");
   if(a.audioEntries.size()!=1||!a.audioSelection.getSelectedItem().toString().contains("Revolution20.mp3"))throw new AssertionError("Projected sequence filename lost");
   if(!"/v1/audio/saved/tracks/lesson-20".equals(a.audioPath(0)))throw new AssertionError("Download must use isolated source workflow");
   a.clearAudioSelection();if(!a.audioBrowseWorkflow.isEmpty()||!a.audioZipPath.isEmpty())throw new AssertionError("Course changes must clear audio selection");
  }catch(Exception e){throw new RuntimeException(e);}});
  report.putString("result","PASS: tabs, setup separation, stage model selection and isolated audio browsing");finish(0,report);
 }catch(Throwable e){report.putString("result","FAIL: "+e);finish(1,report);}finally{if(app!=null){MainActivity finalApp=app;runOnMainSync(finalApp::finish);}}}
}
