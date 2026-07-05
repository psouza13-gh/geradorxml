/**
 * Integrações de rastreamento (GTM, Microsoft Clarity, etc.) — injeta nas
 * páginas públicas os snippets configurados no /admin → Integrações.
 *
 * Por que dinâmico: os códigos ficam no banco (app_settings, key
 * 'site_integrations') e são editáveis pelo admin sem novo deploy — mesmo
 * padrão do meta-pixel.js.
 *
 * Detalhe importante: <script> inserido via innerHTML NÃO executa (regra do
 * HTML5). Por isso cada <script> do snippet é recriado como um elemento novo
 * (copiando atributos e conteúdo) antes de ser anexado — assim GTM, Clarity
 * e afins realmente rodam. O <noscript> do GTM só tem efeito para visitantes
 * sem JavaScript (que nunca chegariam até aqui), então é anexado como está,
 * sem tratamento especial.
 */
(function () {
  function inject(html, target) {
    if (!html || !target) return;
    var tpl = document.createElement('template');
    tpl.innerHTML = html;

    // Recria cada <script> (inclusive dentro de outros elementos) para que execute.
    var scripts = tpl.content.querySelectorAll('script');
    for (var i = 0; i < scripts.length; i++) {
      var old = scripts[i];
      var s = document.createElement('script');
      for (var j = 0; j < old.attributes.length; j++) {
        s.setAttribute(old.attributes[j].name, old.attributes[j].value);
      }
      s.text = old.text || '';
      old.parentNode.replaceChild(s, old);
    }
    target.appendChild(tpl.content);
  }

  fetch('/api/public/integrations')
    .then(function (r) { return r.json(); })
    .then(function (cfg) {
      if (!cfg) return;
      inject(cfg.head, document.head);
      if (document.body) {
        inject(cfg.body, document.body);
      } else {
        document.addEventListener('DOMContentLoaded', function () {
          inject(cfg.body, document.body);
        });
      }
    })
    .catch(function () { /* integração indisponível — segue sem rastreamento */ });
})();
