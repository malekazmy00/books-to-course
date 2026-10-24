package com.book2course.operator;
/** Keep the public PDF identity separate from legacy IDs inside immutable artifacts. */
final class BookIdentity {
 static String publicId(String id,String fileSha){
  if(fileSha!=null&&fileSha.matches("[0-9a-f]{64}"))return "book-"+fileSha.substring(0,24);
  return id==null?"":id;
 }
 static String artifactId(String publicId,String artifactId){
  return artifactId==null||artifactId.trim().isEmpty()?publicId:artifactId;
 }
 static String progressKey(String courseId,String bookId,String ingestId){
  return "progress:"+courseId+":"+bookId+":"+ingestId;
 }
 static boolean sameContext(String courseId,String bookId,String savedCourseId,String savedBookId){
  return courseId.equals(savedCourseId)&&bookId.equals(savedBookId);
 }
}
