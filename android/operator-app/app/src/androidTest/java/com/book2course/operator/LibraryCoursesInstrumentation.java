package com.book2course.operator;
import android.app.Instrumentation;
import android.os.Bundle;
import android.widget.EditText;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.concurrent.Callable;

/** Offline controls regression: no request is sent to a live processing server. */
public class LibraryCoursesInstrumentation extends Instrumentation {
 static class OfflineLibrary extends MainActivity {
  String path=""; ArrayList<JSONObject> displayed; Throwable failure;
  @Override void run(String title,Callable<String> task){try{task.call();}catch(Throwable e){failure=e;}}
  @Override JSONObject get(String path)throws Exception{this.path=path;return new JSONObject().put("books",
   new JSONArray().put(new JSONObject().put("book_id","book-stable").put("course_id","origin").put("title","Saved book")));}
  @Override void showGlobalLibrary(ArrayList<JSONObject> books){displayed=books;}
  @Override void renderScreen(int screen){}
 }
 public void onCreate(Bundle arguments){super.onCreate(arguments);start();}
 public void onStart(){Bundle result=new Bundle();try{
  runOnMainSync(()->{try{
   OfflineLibrary a=new OfflineLibrary();
   a.p=getTargetContext().getSharedPreferences("library-regression",0);
   a.p.edit().clear().commit();
   a.course=new EditText(getTargetContext());a.book=new EditText(getTargetContext());a.title=new EditText(getTargetContext());
   a.course.setText("misspelled-course");
   a.loadBooks();
   if(a.failure!=null)throw new AssertionError(a.failure);
   if(!"/v1/library/books".equals(a.path)||a.displayed==null||a.displayed.size()!=1)
    throw new AssertionError("Library lookup must be independent of current course name");
   a.ingest="old-ingest";a.index="old-index";a.qa="old-qa";a.translation="old-translation";a.audio="old-audio";
   a.switchCourse(new JSONObject().put("course_id","course-new").put("name","New course"));
   if(!"course-new".equals(a.course.getText().toString())||!a.book.getText().toString().isEmpty()
     ||!a.ingest.isEmpty()||!a.index.isEmpty()||!a.qa.isEmpty()||!a.translation.isEmpty()||!a.audio.isEmpty())
    throw new AssertionError("Switching course must never retain another course workflow");
   if(!"New course".equals(a.p.getString("course_name","")))throw new AssertionError("Course display name not persisted");
   a.io.shutdownNow();a.monitorIo.shutdownNow();a.statusTimer.shutdownNow();
  }catch(Exception e){throw new RuntimeException(e);}});
  result.putString("result","PASS: independent global library and safe course switch");finish(0,result);
 }catch(Throwable e){result.putString("result","FAIL: "+e);finish(1,result);}}
}
