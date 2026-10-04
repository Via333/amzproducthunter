import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = 'http://127.0.0.1:8765';
const output = process.env.PLAYWRIGHT_OUTPUT || resolve('output/playwright/keyword-search');
await mkdir(output, {recursive:true});
const browser = await chromium.launch({headless:true, channel:'chrome'});
let realResearchCalls = 0;
try {
  for (const viewport of [{width:1440,height:1000}, {width:390,height:844}]) {
    const page = await browser.newPage({viewport});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let jobs = [], posts = [], offline = false, resultReads = 0;
    const fixture = {summary:{run_id:'fixture-1',keyword:'Rolling Pin',raw_result_count:20,eligible_candidate_count:1,
      go_count:0,watch_count:1,top_score:60,top_title:'Fixture Rolling Pin',searched_at:'2026-09-07T00:00:00',
      report_md:'keyword/fixture/report.md',ranked_csv:'keyword/fixture/data.csv'},
      rows:[{asin:'B0FIXTURE1', title:'Fixture Rolling Pin', listing_url:'https://www.amazon.com/dp/B0FIXTURE1',
        category:'Rolling Pins', score:60, recommendation:'Watch', monthly_sales:500,reviews:81,price:19.99,flags:'Fixture only'}]};
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      const reply = value => route.fulfill({contentType:'application/json', body:JSON.stringify(value)});
      if (url.pathname === '/api/workbench') {
        if (offline) return route.abort();
        return reply({token:'fixture-token',favorites:[],jobs:[],dismissals:[],keyword_jobs:jobs});
      }
      if (url.pathname === '/api/keyword-search') {
        assert.equal(req.headers()['x-workbench-token'], 'fixture-token');
        posts.push(req.postDataJSON());
        const job = {id:posts.length,keyword:posts.at(-1).keyword,status:'queued',message:''};
        jobs = [job];
        await new Promise(resolve=>setTimeout(resolve,100));
        return reply(job);
      }
      if (url.pathname === '/api/keyword-search/results') { resultReads++; return reply(fixture); }
      if (url.pathname === '/api/research/cancel') { jobs[0].status='cancelled'; return reply({ok:true}); }
      if (req.method() !== 'GET' || url.pathname.startsWith('/api/')) { realResearchCalls++; return route.abort(); }
      return req.url().startsWith(base) ? route.continue() : route.abort();
    });
    await page.goto(base+'/index.html#keyword-search', {waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('#shapeWorkbenchConnection')?.textContent.includes('已连接'));
    assert.equal(await page.locator('#keywordSearchSubmit').isDisabled(), true);
    await page.locator('#keywordSearchInput').fill('Rolling Pin');
    await page.locator('#keywordSearchInput').press('Enter');
    await page.evaluate(()=>{document.querySelector('#keywordSearchForm').requestSubmit();document.querySelector('#keywordSearchForm').requestSubmit();});
    await page.waitForFunction(()=>document.querySelector('#keywordSearchSubmit').textContent.includes('排队中'));
    assert.equal(posts.length,1);
    assert.equal(await page.locator('#keywordSearchSubmit').isDisabled(),true);
    jobs[0].status='running';
    await page.evaluate(()=>window.AmzWorkbench.sync());
    assert.match(await page.locator('#keywordSearchStatus').innerText(),/搜索中/);
    jobs[0].status='completed';
    jobs[0].message='fixture completed';
    await page.evaluate(()=>window.AmzWorkbench.sync());
    await page.waitForFunction(()=>document.querySelector('#keywordResultBody')?.textContent.includes('Fixture Rolling Pin'));
    assert.equal(resultReads,1);
    assert.match(await page.locator('#keywordSearchHistoryBody').innerText(),/Rolling Pin/);
    assert.match(await page.locator('#keywordSearchStatus').innerText(),/20 个相关产品/);
    await page.locator('#keyword-search').screenshot({path:`${output}/${viewport.width}-completed.png`});
    for (const selector of ['#keywordSearchInput','#keywordSearchSubmit']) {
      const rect=await page.locator(selector).boundingBox();
      assert(rect.x>=0 && rect.x+rect.width<=viewport.width+1, `${selector} overflow`);
    }
    await page.locator('#keywordSearchSubmit').click();
    await page.waitForFunction(()=>document.querySelector('#keywordSearchSubmit').textContent.includes('排队中'));
    assert.equal(posts.length,2);
    assert.notEqual(posts[0].request_id,posts[1].request_id);
    jobs[0].status='failed'; jobs[0].message='Fixture timeout';
    await page.evaluate(()=>window.AmzWorkbench.sync());
    assert.match(await page.locator('#keywordSearchStatus').innerText(),/失败/);
    await page.locator('[data-keyword-retry]').click();
    await page.waitForFunction(()=>document.querySelector('#keywordSearchSubmit').textContent.includes('排队中'));
    assert.equal(posts.length,3);
    await page.locator('[data-keyword-cancel]').click();
    await page.waitForFunction(()=>document.querySelector('#keywordSearchJobs').textContent.includes('已取消'));
    offline=true;
    await page.evaluate(()=>window.AmzWorkbench.sync());
    assert.equal(await page.locator('#keywordSearchSubmit').isDisabled(),true);
    offline=false;
    await page.evaluate(()=>window.AmzWorkbench.sync());
    assert.equal(await page.locator('#keywordSearchSubmit').isDisabled(),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({viewport, mockedSubmissions:posts.length,resultReads,errors}));
    await page.close();
  }
  const page=await browser.newPage({viewport:{width:390,height:844}});
  await page.route('**/*',route=>route.request().url().startsWith('file:')?route.continue():route.abort());
  await page.goto(pathToFileURL(resolve('web/index.html')).href+'#keyword-search',{waitUntil:'domcontentloaded'});
  await page.locator('#keywordSearchInput').fill('Rolling Pin');
  assert.equal(await page.locator('#keywordSearchSubmit').isDisabled(),true);
  const href=await page.locator('#keywordSearchStatus a').getAttribute('href');
  assert.equal(new URL(href).searchParams.get('keyword'),'Rolling Pin');
  assert.equal(new URL(href).origin,base);
  assert.equal(new URL(href).hash,'#keyword-search');
  console.log(JSON.stringify({fileMode:'safe service link',realResearchCalls}));
  assert.equal(realResearchCalls,0);
} finally { await browser.close(); }
