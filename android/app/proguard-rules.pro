# Nothing in the app is reached by reflection: no @JavascriptInterface (the
# page talks to the app through WebViewCompat.addWebMessageListener), no
# serialization. R8's defaults are enough.
