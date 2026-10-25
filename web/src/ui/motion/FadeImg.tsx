// Снимок, что проявляется после загрузки: пока грузится — скелетон, при ошибке — тихая заглушка.
// Только для обычных картинок: у живого MJPEG событие load приходит не везде.
import { useState, type ImgHTMLAttributes } from "react";

type Props = ImgHTMLAttributes<HTMLImageElement> & {
  /** Текст заглушки, если картинка не загрузилась; null — просто убрать картинку. */
  fallback?: string | null;
};

type Phase = "loading" | "loaded" | "error";

export default function FadeImg({ fallback = "нет кадра", className, onLoad, onError, src, ...rest }: Props) {
  const [state, setState] = useState<{ src: typeof src; phase: Phase }>({ src, phase: "loading" });
  // новый src — снова грузим (без эффекта: состояние сверяется при рендере)
  if (state.src !== src) setState({ src, phase: "loading" });
  const phase: Phase = state.src === src ? state.phase : "loading";
  if (phase === "error") {
    if (fallback === null) return null;
    return (
      <span className={`fx-img-error ${className ?? ""}`} role="img" aria-label={rest.alt}>
        {fallback}
      </span>
    );
  }
  return (
    <img
      loading="lazy"
      decoding="async"
      {...rest}
      src={src}
      // из кэша картинка бывает готова раньше, чем React повесит onLoad
      ref={(img) => {
        if (img?.complete && img.naturalWidth > 0 && phase === "loading") setState({ src, phase: "loaded" });
      }}
      className={`fx-img ${phase === "loaded" ? "is-loaded" : "skeleton"} ${className ?? ""}`}
      onLoad={(e) => {
        setState({ src, phase: "loaded" });
        onLoad?.(e);
      }}
      onError={(e) => {
        setState({ src, phase: "error" });
        onError?.(e);
      }}
    />
  );
}
