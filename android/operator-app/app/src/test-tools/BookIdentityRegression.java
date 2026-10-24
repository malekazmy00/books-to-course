package com.book2course.operator;
public final class BookIdentityRegression {
 static void yes(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  String hash="aaea5c4b55e6a7519bdea580fcab7075b0778da690bbe3534e97279a5c447e01",stable="book-aaea5c4b55e6a7519bdea580";
  yes(BookIdentity.publicId("book-main",hash).equals(stable));
  yes(BookIdentity.publicId(stable,hash).equals(stable));
  yes(BookIdentity.artifactId(stable,"book-main").equals("book-main"));
  yes(BookIdentity.artifactId(stable,"").equals(stable));
  yes(!BookIdentity.progressKey("course1",stable,"ingest1").equals(BookIdentity.progressKey("course2",stable,"ingest1")));
  yes(!BookIdentity.progressKey("course1",stable,"ingest1").equals(BookIdentity.progressKey("course1","book-other","ingest1")));
  yes(!BookIdentity.sameContext("course1",stable,"course1","book-main"));
  yes(BookIdentity.sameContext("course1",stable,"course1",stable));
  yes(BookIdentity.publicId("book-existing","invalid").equals("book-existing"));
  System.out.println("PASS: stable PDF IDs, legacy artifact IDs, course/book cache isolation, persisted legacy context rejection");
 }
}
