// Browser contract checks against the pinned executor/stage shadow-DOM structure.
// Run with Playwright installed; PLAYWRIGHT_MODULE can select an existing install.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {readFileSync} = require('node:fs');
const {spawnSync} = require('node:child_process');
const {resolve} = require('node:path');
const assert = require('node:assert/strict');
const root = resolve(__dirname, '..');
const assets = resolve(root, 'ansible/playbooks/roles/authentik/files/branding');
const render = spawnSync('python3', ['-c',
    'import sys; sys.path.insert(0,"tests"); from test_authentik_branding import render_flow; print(render_flow())'],
    {cwd: root, encoding: 'utf8'});
assert.equal(render.status, 0, render.stderr);
const html = render.stdout;
const css = readFileSync(resolve(assets, 'login-v1.css'), 'utf8');
const behavior = readFileSync(resolve(assets, 'login-v1.js'), 'utf8');

// Minimal native component contracts, with the actual 2025.10.4 selectors and
// challenge/source order. Tests check our integration, not Authentik credentials.
const native = `
const sheet = new CSSStyleSheet(); sheet.replaceSync(${JSON.stringify(css)});
class Identification extends HTMLElement {
  constructor() { super(); this.attachShadow({mode:'open'}); this.updateComplete = Promise.resolve(); }
  connectedCallback() {
    this.shadowRoot.adoptedStyleSheets = [sheet];
    this.shadowRoot.innerHTML = '<ak-flow-card><form class="pf-c-form"><label for="uid">Username</label><input id="uid" name="uidField" autocomplete="username" required><button type="submit" class="pf-c-button">Continue</button></form><ul slot="footer" class="pf-c-login__main-footer-links">'+
      window.executor.challenge.sources.map(s => '<li class="pf-c-login__main-footer-links-item"><button type="button">'+s.name+'</button></li>').join('')+'</ul></ak-flow-card>';
    this.shadowRoot.querySelector('form').addEventListener('submit', e => {
      e.preventDefault(); window.nativeSubmissions++;
      window.executor.show('ak-stage-password');
    });
    this.shadowRoot.querySelectorAll('li button').forEach((button,i) => {
      button.addEventListener('click', () => {window.selectedSource = window.executor.challenge.sources[i].name; window.executor.show('xak-flow-redirect');});
    });
  }
}
customElements.define('ak-stage-identification', Identification);
class Card extends HTMLElement {
 constructor() {super(); this.attachShadow({mode:'open'}); this.shadowRoot.adoptedStyleSheets = [sheet]; this.shadowRoot.innerHTML='<div class="pf-c-login__main-header">Native flow title</div><div class="pf-c-login__main-body"><slot></slot></div><footer><slot name="footer"></slot></footer>';}
}
customElements.define('ak-flow-card', Card);
class Executor extends HTMLElement {
 constructor(){ super(); this.attachShadow({mode:'open'}); this.updateComplete=Promise.resolve(); this.challenge={sources:[{name:'Other source'},{name:'Google'}]}; }
 connectedCallback(){window.executor=this; window.nativeSubmissions=0; this.shadowRoot.adoptedStyleSheets=[sheet]; setTimeout(()=>this.show('ak-stage-identification'),10);}
 show(component, sources=this.challenge.sources, errors={}) {
  this.challenge={component,sources,responseErrors:errors};
  this.shadowRoot.innerHTML='<div class="pf-c-login"><div class="ak-login-container"><main class="pf-c-login__main">'+(component==='ak-stage-identification'?'<ak-stage-identification></ak-stage-identification>':component==='ak-stage-password'?'<label>Password <input type="password" autocomplete="current-password"></label>':component==='ak-stage-access-denied'?'<p role="alert">Access denied</p>':'<p>Redirecting</p>')+'</main></div></div>';
 }
}
customElements.define('ak-flow-executor',Executor);
`;

(async () => {
    const browser = await chromium.launch({headless:true,
        ...(process.env.CHROMIUM_BIN ? {executablePath:process.env.CHROMIUM_BIN} : {})});
    try {
        const page = await browser.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('https://auth.example.invalid/**', async route => {
            const path = new URL(route.request().url()).pathname;
            if (path === '/media/branding/login-v1.css') return route.fulfill({body:css,contentType:'text/css'});
            if (path === '/media/branding/login-v1.js') return route.fulfill({body:behavior,contentType:'text/javascript'});
            if (path === '/media/branding/logo-v1.png') return route.fulfill({body:readFileSync(resolve(assets,'logo-v1.png')),contentType:'image/png'});
            if (path.startsWith('/static/')) return route.fulfill({body:native,contentType:'text/javascript'});
            return route.fulfill({body:html,contentType:'text/html'});
        });
        for (const width of [360,390,728,1280]) {
            await page.setViewportSize({width,height:900});
            await page.goto('https://auth.example.invalid/if/flow/default-authentication-flow/?next=%2Fapplication%2Fo%2Fauthorize%2F%3Fstate%3Dfixture');
            await page.locator('#pp-google').waitFor({state:'visible'});
            assert.equal(await page.getByLabel('Username',{exact:true}).isVisible(),false);
            assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
            const next = await page.evaluate(()=>new URL(location.href).searchParams.get('next'));
            await page.locator('#pp-admin').click();
            await page.getByLabel('Username',{exact:true}).waitFor({state:'visible'});
            assert.equal(await page.locator('#pp-google').isVisible(),false);
            assert.equal(await page.evaluate(()=>new URL(location.href).searchParams.get('next')),next);
            assert.equal(await page.evaluate(()=>new URL(location.href).searchParams.get('pp-local')),'1');
            await page.reload();
            await page.getByLabel('Username',{exact:true}).waitFor({state:'visible'});
            await page.locator('#pp-admin').click();
            await page.locator('#pp-google').waitFor({state:'visible'});
            await page.locator('#pp-google').click();
            await page.getByText('Redirecting',{exact:true}).waitFor();
            assert.equal(await page.evaluate(()=>window.selectedSource),'Google');
            assert.equal(await page.locator('#pp-admin').isVisible(),false);
        }
        await page.evaluate(()=>window.executor.show('ak-stage-identification'));
        await page.locator('#pp-admin').waitFor({state:'visible'});
        await page.locator('#pp-admin').click();
        await page.getByLabel('Username',{exact:true}).fill('fixture-admin');
        await page.getByRole('button',{name:'Continue',exact:true}).click();
        await page.getByLabel('Password',{exact:true}).waitFor({state:'visible'});
        assert.equal(await page.evaluate(()=>window.nativeSubmissions),1);
        await page.evaluate(()=>window.executor.show('ak-stage-identification',[],{}));
        await page.getByLabel('Username',{exact:true}).waitFor({state:'visible'});
        assert.equal(await page.locator('#pp-google').isVisible(),false);
        await page.evaluate(()=>window.executor.show('ak-stage-identification',[{name:'Google'}],{uid_field:['Invalid account']}));
        await page.getByLabel('Username',{exact:true}).waitFor({state:'visible'});
        await page.evaluate(()=>window.executor.show('ak-stage-access-denied'));
        await page.getByRole('alert').waitFor({state:'visible'});
        assert.equal(await page.locator('#pp-google').isVisible(),false);
        assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
        assert.deepEqual(errors,[]);
        console.log('PASS: Google-only default, correct native source, native admin/password stages, bookmarked local view, preserved return query, missing-source/error fallback; four viewport widths.');
    } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exit(1);});
