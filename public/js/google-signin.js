/**
 * Botão "Entrar com Google" (Google Identity Services) — usado em /login e
 * /register.
 *
 * Só ativa se GOOGLE_CLIENT_ID estiver configurado no servidor (consultado em
 * /api/public/google-client-id) — sem a env var, nada é exibido e as páginas
 * ficam exatamente como antes. O Google devolve uma credencial (JWT assinado)
 * que é enviada a POST /api/auth/google; o backend valida a assinatura e
 * responde com o mesmo token/estrutura do login por senha.
 *
 * Depende dos globais das páginas: showErr/hideErr e as chaves gxml_token /
 * gxml_user no localStorage (mesmo fluxo de sessão do login normal).
 */
(function () {
  // Sessão ativa → o banner "você já está conectado" da página assume; não
  // faz sentido oferecer outro caminho de login por baixo dele.
  if (localStorage.getItem('gxml_token')) return;

  var wrap = document.getElementById('google-wrap');
  var slot = document.getElementById('google-btn');
  if (!wrap || !slot) return;

  function onCredential(response) {
    if (typeof hideErr === 'function') hideErr();
    fetch('/api/auth/google', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential: response.credential })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          if (typeof showErr === 'function') showErr(res.data.error || 'Erro ao entrar com Google. Tente novamente.');
          return;
        }
        localStorage.setItem('gxml_token', res.data.token);
        if (res.data.user) localStorage.setItem('gxml_user', JSON.stringify(res.data.user));
        window.location.replace('/app');
      })
      .catch(function () {
        if (typeof showErr === 'function') showErr('Erro de conexão. Verifique sua internet e tente novamente.');
      });
  }

  fetch('/api/public/google-client-id')
    .then(function (r) { return r.json(); })
    .then(function (cfg) {
      if (!cfg || !cfg.client_id) return;

      var s = document.createElement('script');
      s.src = 'https://accounts.google.com/gsi/client';
      s.async = true;
      s.defer = true;
      s.onload = function () {
        if (!window.google || !google.accounts || !google.accounts.id) return;
        google.accounts.id.initialize({
          client_id: cfg.client_id,
          callback: onCredential
        });
        wrap.style.display = '';
        google.accounts.id.renderButton(slot, {
          theme: 'outline',
          size: 'large',
          shape: 'pill',
          text: 'continue_with',
          locale: 'pt-BR',
          width: Math.max(200, Math.min(360, slot.clientWidth || 320))
        });
      };
      document.head.appendChild(s);
    })
    .catch(function () { /* não configurado / indisponível — página segue normal */ });
})();
