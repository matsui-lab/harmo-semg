// Browser-standard file input: source files are copied only to this PC's service.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const home=new URL(process.argv[2]),project=process.argv[3];
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1100,height:960}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const headers={Authorization:'Bearer '+home.hash.slice(1),Origin:home.origin,'Content-Type':'application/octet-stream'};
  assert.equal((await page.request.post(home.origin+'/api/import',{data:Buffer.from('x')})).status(),403);
  assert.equal((await page.request.post(home.origin+'/api/import',{headers:{...headers,Origin:'https://example.org'},data:Buffer.from('x')})).status(),403);
  assert.equal((await page.request.post(home.origin+'/api/import',{headers:{...headers,'Content-Type':'text/plain'},data:Buffer.from('x')})).status(),400);
  await page.goto(home.href);
  await page.locator('#version').filter({hasText:'local-browser.1'}).waitFor();
  assert.equal(await page.locator('#workspace').count(),0);
  await page.screenshot({path:'/private/tmp/harmo-local-browser-launcher.png'});
  await page.locator('#projectFile').setInputFiles(project);
  await page.locator('#continueRun').waitFor({timeout:120000});
  assert.equal(await page.locator('#reviewer').count(),0);
  assert.equal(await page.evaluate(()=>edits.decisions.every(d=>d.action==='unreviewed')),true);
  await page.screenshot({path:'/private/tmp/harmo-local-browser-imported.png'});
  await page.locator('#continueRun').click();
  await page.locator('#evaluate').click();
  await page.waitForFunction(()=>!running,{timeout:120000});
  assert.equal(await page.evaluate(()=>activeRun.phase),'evaluated');
  await page.locator('#execute').click();
  await page.waitForFunction(()=>!running,{timeout:120000});
  const result=await page.evaluate(()=>activeRun);
  assert.equal(result.phase,'executed');assert(result.curves>0);assert.equal(result.review_metrics.reviewed_items,0);
  assert.equal(result.scientific_approval,false);
  await page.locator('#registerResult').click();
  await page.waitForFunction(()=>!running,{timeout:120000});
  assert.equal(await page.locator('#registerResult').innerText(),'Saved');
  await page.locator('nav a').click();
  await page.locator('#projects .row').first().waitFor();
  await page.locator('#quit').click();
  await page.getByText('Harmo has stopped. You can close this tab.').waitFor();
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',browser_file_import:true,curves:result.curves,comparisons:result.comparisons,human_confirmations:0,scientific_approval:false}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
