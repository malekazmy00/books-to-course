package com.book2course.operator;
import java.net.URI;
import java.util.*;
import java.io.IOException;

/** A configured HTTPS origin plus an explicit same-host port backup. */
final class EndpointRouting {
 static final String TRUSTED_HOST="desktop-3khe91s.tail7385b1.ts.net";
 interface HealthProbe { boolean healthy(String origin) throws IOException; }
 static String normalize(String raw) {
  try {
   URI u=new URI(raw.trim());
   if(!"https".equalsIgnoreCase(u.getScheme())||u.getHost()==null||u.getRawUserInfo()!=null||u.getRawQuery()!=null||u.getRawFragment()!=null
       ||(u.getRawPath()!=null&&!u.getRawPath().isEmpty()&&!"/".equals(u.getRawPath()))||u.getPort()==0||u.getPort() < -1||u.getPort()>65535)
    throw new IllegalArgumentException("استخدم عنوان HTTPS للخادم دون مسار أو بيانات دخول.");
   return new URI("https",null,u.getHost().toLowerCase(Locale.ROOT),u.getPort()==443?-1:u.getPort(),null,null,null).toASCIIString();
  }catch(Exception e){throw new IllegalArgumentException("استخدم عنوان HTTPS صالحًا للخادم دون مسار أو بيانات دخول.");}
 }
 static List<String> candidates(String configured) {
  String origin=normalize(configured); List<String> list=new ArrayList<>();list.add(origin);
  URI u=URI.create(origin);
  if(TRUSTED_HOST.equals(u.getHost())&&(u.getPort()==-1||u.getPort()==8443)){
   list.add("https://"+TRUSTED_HOST+(u.getPort()==8443?"":":8443"));
  }
  return list;
 }
 static String select(String configured,String preferred,HealthProbe probe)throws IOException{
  List<String> options=candidates(configured);
  if(preferred!=null&&options.remove(preferred))options.add(0,preferred);
  for(String option:options){try{if(probe.healthy(option))return option;}catch(IOException ignored){}}
  throw new IOException("تعذر الاتصال بالخادم عبر المسارات المتاحة؛ لم يُرسل طلب تشغيل.");
 }
 static boolean sameHost(String left,String right){return URI.create(normalize(left)).getHost().equals(URI.create(normalize(right)).getHost());}
}
