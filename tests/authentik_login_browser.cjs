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
const css = readFileSync(process.env.AUTHENTIK_TEST_CSS || resolve(assets, 'login-v2.css'), 'utf8');
const behavior = readFileSync(resolve(assets, 'login-v2.js'), 'utf8');

// Native layout constraints from 2025.10.4: the drawer has a 450px desktop
// minimum, the stacked container fills the viewport, and controls own their
// theme variables. Keep these constraints so the outer card cannot mask overflow.
const nativeCss = `
* { box-sizing:border-box; }
.pf-c-drawer { display:flex; flex-direction:column; height:100%; min-width:50%; }
@media(min-width:1200px) { .pf-c-drawer { min-width:28.125rem; } }
.pf-c-drawer__main, .pf-c-drawer__content { display:flex; flex:1; }
.pf-c-drawer__body { flex:1; }
.ak-login-container { height:calc(100vh - 3rem); }
.pf-c-login__main-body { padding:0 16px 32px; }
.pf-c-form { display:grid; gap:24px; }
.pf-c-form-control, .pf-m-block { width:100%; }
.pf-c-button { display:inline-block; text-align:center; padding:6px 16px; }
.pf-c-button.pf-m-primary { --pf-c-button--m-primary--BackgroundColor:#06c; background:var(--pf-c-button--m-primary--BackgroundColor); color:white; }
.pf-c-button.pf-m-primary:hover { background:#004080; }
.pf-c-button.pf-m-secondary { color:#06c; }
.pf-c-button.pf-m-secondary::after { content:''; border:1px solid #06c; }
.pf-c-empty-state { padding:48px; text-align:center; }
.pf-c-empty-state__icon { width:48px; height:48px; margin:0 auto 16px; }
`;
// Navigation uses disposable component doubles; no credentials are submitted.
const native = `
const sheet = new CSSStyleSheet(); sheet.replaceSync(${JSON.stringify(nativeCss + css)});
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
 constructor() {super(); this.attachShadow({mode:'open'}); this.shadowRoot.adoptedStyleSheets = [sheet];}
 connectedCallback() {this.shadowRoot.innerHTML='<div class="pf-c-login__main-header"><slot name="title">Native flow title</slot></div><div class="pf-c-login__main-body">'+(this.hasAttribute('loading')?'<ak-empty-state loading></ak-empty-state>':'<slot></slot>')+'</div><footer class="pf-c-login__main-footer"><slot name="footer"></slot></footer>';}
}
customElements.define('ak-flow-card', Card);
class EmptyState extends HTMLElement {
 constructor(){super();this.attachShadow({mode:'open'});this.shadowRoot.adoptedStyleSheets=[sheet];this.shadowRoot.innerHTML='<div class="pf-c-empty-state"><div class="pf-c-empty-state__content"><div class="pf-c-empty-state__icon">◌</div><h1>Loading</h1></div></div>';}
}
customElements.define('ak-empty-state',EmptyState);
class Password extends HTMLElement {
 constructor(){super();this.attachShadow({mode:'open'});this.shadowRoot.adoptedStyleSheets=[sheet];this.shadowRoot.innerHTML='<ak-flow-card><form class="pf-c-form"><label>Password <input type="password" autocomplete="current-password" class="pf-c-form-control"></label><button class="pf-c-button pf-m-primary pf-m-block">Continue</button></form></ak-flow-card>';}
}
customElements.define('ak-stage-password',Password);
class SessionEnd extends HTMLElement {
 constructor(){super();this.attachShadow({mode:'open'});this.shadowRoot.adoptedStyleSheets=[sheet];this.shadowRoot.innerHTML='<ak-flow-card><h1 slot="title">You have logged out of Monitoring.</h1><form class="pf-c-form"><p>administrator-with-a-long-account-name@example.invalid</p><p>You can return to the overview or log out of your account.</p><a href="#overview" class="pf-c-button pf-m-primary">Go back to overview</a><a href="#logout" class="pf-c-button pf-m-secondary">Log out of Pearland Paws</a><a href="#application" class="pf-c-button pf-m-secondary">Log back into Monitoring</a></form></ak-flow-card>';}
}
class Executor extends HTMLElement {
 constructor(){ super(); this.attachShadow({mode:'open'}); this.updateComplete=Promise.resolve(); this.challenge={sources:[{name:'Other source'},{name:'Google'}]}; }
 connectedCallback(){window.executor=this; window.nativeSubmissions=0; this.shadowRoot.adoptedStyleSheets=[sheet]; setTimeout(()=>this.show('ak-stage-identification'),10);}
 show(component, sources=this.challenge.sources, errors={}) {
  this.challenge={component,sources,responseErrors:errors};
  if(component==='ak-stage-session-end'&&!customElements.get(component)) setTimeout(()=>customElements.define(component,SessionEnd),10);
  const body=component==='loading'?'<ak-flow-card loading></ak-flow-card>':component==='ak-stage-identification'?'<ak-stage-identification></ak-stage-identification>':component==='ak-stage-password'?'<ak-stage-password></ak-stage-password>':component==='ak-stage-session-end'?'<ak-stage-session-end></ak-stage-session-end>':component==='ak-stage-access-denied'?'<p role="alert">Access denied</p>':'<p>Redirecting</p>';
  this.shadowRoot.innerHTML='<div class="pf-c-page__drawer"><div class="pf-c-drawer"><div class="pf-c-drawer__main"><div class="pf-c-drawer__content"><div class="pf-c-drawer__body"><div class="pf-c-login"><div class="ak-login-container"><main class="pf-c-login__main">'+body+'</main></div></div></div></div></div></div></div>';
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
        async function assertContained() {
            const escaped = await page.locator('.pp-card').evaluate(card => {
                const box=card.getBoundingClientRect();
                const padding=parseFloat(getComputedStyle(card).paddingLeft);
                const escaped=[];
                function walk(root) {
                    for(const el of root.querySelectorAll('*')) {
                        if(el.shadowRoot) walk(el.shadowRoot);
                        const r=el.getBoundingClientRect();
                        if(!r.width||!r.height||getComputedStyle(el).visibility==='hidden') continue;
                        if(r.left<box.left+padding-1||r.right>box.right-padding+1||r.bottom>box.bottom-1)
                            escaped.push(el.localName+'.'+el.className);
                    }
                }
                walk(card.querySelector('ak-flow-executor').shadowRoot);
                return escaped;
            });
            assert.deepEqual(escaped,[], 'Native content must fit inside the card padding');
        }
        await page.route('https://auth.example.invalid/**', async route => {
            const path = new URL(route.request().url()).pathname;
            if (path === '/media/branding/login-v2.css') return route.fulfill({body:css,contentType:'text/css'});
            if (path === '/media/branding/login-v2.js') return route.fulfill({body:behavior,contentType:'text/javascript'});
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
            await assertContained();
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
            await page.evaluate(()=>window.executor.show('ak-stage-session-end'));
            const overview=page.getByRole('link',{name:'Go back to overview',exact:true});
            await overview.waitFor();
            await assertContained();
            assert.equal(await overview.evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(254, 205, 211)');
            await overview.hover();
            assert.equal(await overview.evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(253, 164, 175)');
            await page.evaluate(()=>window.executor.show('loading'));
            await page.getByText('Loading',{exact:true}).waitFor();
            await assertContained();
            const loading=await page.locator('ak-empty-state').evaluate(el=>{
                const icon=el.shadowRoot.querySelector('.pf-c-empty-state__icon').getBoundingClientRect();
                const host=el.getBoundingClientRect();
                return {offset:Math.abs(icon.x+icon.width/2-host.x-host.width/2),height:host.height};
            });
            assert(loading.offset<1,'Loading indicator must be centered');
            assert(loading.height<250,'Loading state must not fill the viewport');
        }
        await page.evaluate(()=>window.executor.show('ak-stage-identification'));
        await page.locator('#pp-admin').waitFor({state:'visible'});
        await page.locator('#pp-admin').click();
        await page.getByLabel('Username',{exact:true}).fill('fixture-admin');
        await page.getByRole('button',{name:'Continue',exact:true}).click();
        await page.getByLabel('Password',{exact:true}).waitFor({state:'visible'});
        await assertContained();
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
        console.log('PASS: navigation and native fallbacks; administrator/password/signed-out containment, loading alignment and themed hover at four viewport widths.');
    } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exit(1);});
