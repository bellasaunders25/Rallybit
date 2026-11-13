import { createApp } from './app.js';
const { registry } = createApp({ dataDirectory: './data' });
const report = registry.coverage();
console.log(JSON.stringify(report, null, 2));
process.exitCode = report.missing.length ? 1 : 0;
