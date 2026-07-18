import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index: "src/index.ts",
    "integrations/vercel-ai-sdk": "src/integrations/vercel-ai-sdk.ts",
    "integrations/langchain": "src/integrations/langchain.ts",
    "integrations/openai": "src/integrations/openai.ts",
    "integrations/mastra": "src/integrations/mastra.ts",
    "testing/index": "src/testing/index.ts",
    "mcp/index": "src/mcp/index.ts",
  },
  format: ["esm", "cjs"],
  dts: true,
  sourcemap: true,
  clean: true,
  target: "es2022",
  outDir: "dist",
});
