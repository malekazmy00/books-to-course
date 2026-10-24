package com.book2course.operator;
import java.util.*;
import java.io.IOException;
public final class EndpointRoutingRegression {
 static void yes(boolean b){if(!b)throw new AssertionError();}
 public static void main(String[] args)throws Exception {
  String primary="https://"+EndpointRouting.TRUSTED_HOST,backup=primary+":8443";
  yes(EndpointRouting.normalize(primary+":443/").equals(primary));
  yes(EndpointRouting.candidates(primary).equals(Arrays.asList(primary,backup)));
  yes(EndpointRouting.candidates(backup).equals(Arrays.asList(backup,primary)));
  yes(EndpointRouting.candidates("https://other.example").equals(Arrays.asList("https://other.example")));
  yes(!EndpointRouting.sameHost(primary,"https://other.example"));
  List<String> seen=new ArrayList<>();
  yes(EndpointRouting.select(primary,null,x->{seen.add(x);return x.equals(backup);}).equals(backup));
  yes(seen.equals(Arrays.asList(primary,backup)));
  seen.clear();
  yes(EndpointRouting.select(primary,backup,x->{seen.add(x);return true;}).equals(backup));
  yes(seen.equals(Arrays.asList(backup)));
  for(String bad:Arrays.asList("http://other.example","https://name:password@other.example","https://other.example/path","https://other.example?token=x","https://other.example#secret","https://other.example:0","https://other.example:70000","https://")){
   try{EndpointRouting.normalize(bad);throw new AssertionError("Accepted unsafe endpoint");}catch(IllegalArgumentException expected){}
  }
  try{EndpointRouting.select(primary,null,x->{throw new IOException("offline");});throw new AssertionError();}catch(IOException expected){}
  System.out.println("PASS: HTTPS validation, explicit-port normalization, trusted same-host alternatives, custom-host isolation, bounded fallback candidates, unavailable rejection");
 }
}
