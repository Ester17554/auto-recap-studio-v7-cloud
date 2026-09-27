# AUTO RECAP STUDIO V8 — MAPA RÁPIDO LOCAL

V8 é uma PWA estática para iPhone criada para gerar um mapa de cenas sem enviar o filme para um servidor.

## O que mudou
- processamento local no navegador;
- não copia o filme para memória como um blob completo;
- varredura por amostras usando o decodificador nativo do vídeo;
- assinatura visual minúscula (32×18) para detectar mudanças;
- thumbnails somente para candidatos finais (240×135);
- não salva todas as amostras no localStorage;
- mantém múltiplas partes de narração;
- exporta JSON, CSV e plano TXT para CapCut.

## Limitação importante
O V8 é um **mapa de cenas guiado**, não um editor automático e não possui um modelo multimodal completo rodando no iPhone. Ele detecta mudanças visuais e alinha candidatos à progressão do roteiro/narração. Os timestamps devem ser conferidos no filme antes da edição.

O V8 não envia o filme para a internet. Se o Safari/iPhone não conseguir decodificar o codec do arquivo, o app informa o erro.

## Publicação
Pode ser publicado em GitHub Pages como site estático. Todos os arquivos ficam na raiz.
