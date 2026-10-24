package com.book2course.operator;
import android.app.*;import android.content.*;import android.os.*;import java.util.concurrent.*;
public class ProgressConcurrencyInstrumentation extends AllControlsInstrumentation {
 @Override public void onStart(){Bundle result=new Bundle();CountDownLatch release=new CountDownLatch(1);FixtureActivity a=null;
  try {
   a=(FixtureActivity)startActivitySync(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));final FixtureActivity activity=a;
   runOnMainSync(()->{activity.course.setText("audit-course");activity.book.setText("book-first");activity.ingest="b2c-ingest-fixture";activity.pipelineRunning=true;});
   a.io.execute(()->{try{release.await();}catch(InterruptedException ignored){}});
   runOnMainSync(a::loadBooks);waitForRequest(a,"GET /v1/library/books");settle();closeDialogs();
   runOnMainSync(()->activity.showBookMonitor(json("{\"course_id\":\"audit-course\",\"book_id\":\"book-second\",\"title\":\"Second\"}")));
   waitForRequest(a,"GET /v1/courses/audit-course/books/book-second/progress");settle();closeDialogs();
   runOnMainSync(a::loadAudioTracks);waitForRequest(a,"GET /v1/courses/audit-course/audio");settle();
   if(!"book-first".equals(a.book.getText().toString()))throw new AssertionError("Monitoring replaced processing book");
   for(String request:a.requests)if(!request.startsWith("GET "))throw new AssertionError("Read test submitted a mutation: "+request);
   result.putString("result","PASS: library, second-book monitoring and audio refresh while pipeline executor blocked; processing book unchanged; all requests mocked GET.");
   finish(0,result);
  }catch(Throwable e){result.putString("failure",e.toString());finish(1,result);}
  finally {release.countDown();if(a!=null){final FixtureActivity activity=a;runOnMainSync(()->{activity.pipelineRunning=false;activity.finish();});}}
 }
 void waitForRequest(FixtureActivity a,String request)throws Exception{long deadline=System.currentTimeMillis()+8000;while(System.currentTimeMillis()<deadline){synchronized(a.requests){for(String observed:a.requests)if(observed.equals(request)||observed.startsWith(request+" "))return;}Thread.sleep(100);}throw new AssertionError("Missing request: "+request+"; observed "+a.requests);}
}
