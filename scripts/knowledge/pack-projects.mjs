import fs from 'node:fs/promises';
import path from 'node:path';
import { runCli } from 'repomix';

const [catalogPath, outputRoot, ...names] = process.argv.slice(2);
if (!catalogPath || !outputRoot) throw new Error('Usage: node pack-projects.mjs catalog.json output-directory [project ...]');
const catalog = JSON.parse(await fs.readFile(catalogPath, 'utf8'));
const selected = new Set(names);
const ignored = [
  '**/.env', '**/.env.*', '**/*.{pem,key,p12,pfx}', '**/*credentials*',
  '**/*.min.{js,css}', '**/*.{map,csv,tsv,xlsx,xls,pdf,docx,pptx,svg,lock}',
  '**/{package-lock.json,pnpm-lock.yaml,yarn.lock,uv.lock,poetry.lock}',
  '**/{node_modules,vendor,logs,log,backups,backup,.next,_next,dist,build,target}/**',
  '**/app/Sdk/**', '**/public/swagger/**', '**/graphql-env.d.ts',
  '**/*log*.txt', '**/{production_seed.sql,teachcloud.sql,area-data.sql}',
  '**/{tinymce,elementui,layui}/**', '**/{vue.js,jquery.js}',
];
for (const repo of catalog.repositories) {
  if (repo.status !== 'verified' || (selected.size && !selected.has(repo.name))) continue;
  const target = path.resolve(outputRoot, repo.name, repo.commit);
  if (!target.startsWith(path.resolve(outputRoot) + path.sep)) throw new Error('Invalid repository path');
  await fs.mkdir(target, { recursive: true });
  const output = path.join(target, 'code.json');
  const metricsFile = path.join(target, 'metrics.json');
  try {
    const previous = JSON.parse(await fs.readFile(metricsFile, 'utf8'));
    if (previous.tool === 'repomix@1.18.1' && previous.commit === repo.commit && previous.policy === 3 && !previous.compress) {
      await fs.access(output);
      process.stdout.write(JSON.stringify({event: 'cached', name: repo.name, files: previous.files, tokens: previous.tokens}) + '\n');
      continue;
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const result = await runCli([repo.path], repo.path, {
    output, style: 'json', quiet: true, outputShowLineNumbers: true,
    compress: false, gitSortByChanges: false, securityCheck: true,
    skipLocalConfig: true, skipGlobalConfig: true, enableFileProcessors: false,
    confineToBaseDir: true, ignore: ignored.join(','),
  });
  const packed = result.packResult;
  const metrics = {
    tool: 'repomix@1.18.1', policy: 3, compress: false, name: repo.name, commit: repo.commit,
    files: packed.totalFiles, tokens: packed.totalTokens, characters: packed.totalCharacters,
    sourceFiles: packed.safeFilePaths,
    notPacked: repo.files.filter(file => !packed.safeFilePaths.includes(file)),
    ignorePatterns: ignored,
    securityExcluded: packed.suspiciousFilesResults.map(item => item.filePath),
    skipped: packed.skippedFiles,
  };
  await fs.writeFile(metricsFile, JSON.stringify(metrics, null, 2), 'utf8');
  process.stdout.write(JSON.stringify({event: 'packed', name: repo.name, files: metrics.files, tokens: metrics.tokens,
    characters: metrics.characters, securityExcluded: metrics.securityExcluded.length}) + '\n');
}
