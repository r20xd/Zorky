import { useEffect, useRef, type ImgHTMLAttributes } from "react";

/**
 * Картинка с MJPEG-потоком, которая закрывает соединение при размонтировании.
 * Без этого Chrome держит поток открытым после ухода с экрана; на одну вкладку приходится
 * всего 6 соединений, и после нескольких переходов остальные запросы («Загрузка…») ждут очереди.
 *
 * Адрес выставляется в эффекте (а не атрибутом), чтобы двойной запуск эффектов в StrictMode
 * (монтирование, очистка, монтирование) не оставлял картинку с пустым адресом.
 */
export default function MjpegImage({ src, alt = "", ...rest }: ImgHTMLAttributes<HTMLImageElement> & { src: string }) {
  const ref = useRef<HTMLImageElement>(null);

  useEffect(() => {
    const img = ref.current;
    if (!img) return;
    img.hidden = false;
    img.src = src;
    return () => {
      img.removeAttribute("src");
      img.src = ""; // обрывает соединение с потоком
    };
  }, [src]);

  return <img ref={ref} alt={alt} {...rest} />;
}
