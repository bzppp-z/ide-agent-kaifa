/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端基地址，默认 http://localhost:8010。 */
  readonly VITE_API_BASE?: string;
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
