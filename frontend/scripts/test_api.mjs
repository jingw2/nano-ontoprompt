import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

async function apiCall(method, endpoint, body, token) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const headers = { 'Authorization': 'Bearer ' + token };
    if (data) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = Buffer.byteLength(data); }
    const req = http.request({ hostname: 'localhost', port: 8000, path: endpoint, method, headers }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve({ status: res.statusCode, body: JSON.parse(d) }); } catch { resolve({ status: res.statusCode, body: d }); } });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

async function uploadFile(filePath, token) {
  const fileName = path.basename(filePath);
  const fileBytes = fs.readFileSync(filePath);
  const boundary = '----b' + Date.now();
  const CRLF = '\r\n';
  const head = Buffer.from('--' + boundary + CRLF + 'Content-Disposition: form-data; name="file"; filename="' + fileName + '"' + CRLF + 'Content-Type: text/csv' + CRLF + CRLF, 'utf8');
  const tail = Buffer.from(CRLF + '--' + boundary + '--' + CRLF, 'utf8');
  const formData = Buffer.concat([head, fileBytes, tail]);
  return new Promise((resolve, reject) => {
    const headers = { 'Authorization': 'Bearer ' + token, 'Content-Type': 'multipart/form-data; boundary=' + boundary, 'Content-Length': formData.length };
    const req = http.request({ hostname: 'localhost', port: 8000, path: '/api/v2/datasets/upload', method: 'POST', headers }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve({ status: res.statusCode, body: JSON.parse(d) }); } catch { resolve({ status: res.statusCode, body: d }); } });
    });
    req.on('error', reject);
    req.write(formData); req.end();
  });
}

const login = await apiCall('POST', '/api/v1/auth/login', { username: 'admin', password: 'admin123' });
const token = login.body?.data?.access_token;
console.log('Token:', token?.substring(0,20));

const up = await uploadFile(path.resolve(__dirname, '../test_data/供应链/inventory_transactions.csv'), token);
console.log('Upload status:', up.status);
console.log('Upload body:', JSON.stringify(up.body).substring(0, 300));

const dsId = up.body?.data?.id || up.body?.id;
console.log('Dataset ID:', dsId);

const pl = await apiCall('POST', '/api/v2/pipelines', { name: '供应链-库存-Pipeline', source_dataset_id: dsId, route: 'A', spec: {} }, token);
console.log('Pipeline status:', pl.status);
console.log('Pipeline body:', JSON.stringify(pl.body).substring(0, 300));
const plId = pl.body?.data?.id || pl.body?.id;
console.log('Pipeline ID:', plId);
