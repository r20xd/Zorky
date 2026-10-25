import { useCountUp } from "./hooks";

/** Число, которое плавно доезжает до нового значения (хук нельзя звать в map — поэтому компонент). */
export default function CountUp({ value }: { value: number }) {
  return <>{useCountUp(value)}</>;
}
