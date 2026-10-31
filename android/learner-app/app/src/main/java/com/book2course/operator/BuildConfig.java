package com.book2course.operator;

/** Configuration bridge for the embedded operator console. It contains only a server origin. */
public final class BuildConfig {
    private BuildConfig() {}
    public static final String API_BASE_URL = com.book2course.learner.BuildConfig.DEFAULT_API_BASE_URL.isEmpty()
            ? "https://desktop-3khe91s.tail7385b1.ts.net"
            : com.book2course.learner.BuildConfig.DEFAULT_API_BASE_URL;
}
