import hljs from "highlight.js/lib/core";
import accesslog from "highlight.js/lib/languages/accesslog";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import dos from "highlight.js/lib/languages/dos";
import http from "highlight.js/lib/languages/http";
import ini from "highlight.js/lib/languages/ini";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import nginx from "highlight.js/lib/languages/nginx";
import plaintext from "highlight.js/lib/languages/plaintext";
import powershell from "highlight.js/lib/languages/powershell";
import python from "highlight.js/lib/languages/python";
import routeros from "highlight.js/lib/languages/routeros";
import shell from "highlight.js/lib/languages/shell";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
for (const [name, language] of Object.entries({
  accesslog,
  bash,
  css,
  diff,
  dos,
  http,
  ini,
  javascript,
  json,
  nginx,
  plaintext,
  powershell,
  ps1: powershell,
  python,
  py: python,
  routeros,
  shell,
  sh: shell,
  sql,
  typescript,
  ts: typescript,
  xml,
  yaml,
  yml: yaml,
} as Record<string, typeof accesslog>)) {
  hljs.registerLanguage(name, language);
}
