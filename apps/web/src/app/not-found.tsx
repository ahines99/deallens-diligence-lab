import { Button } from "@/components/ui/Button";

export default function NotFound() {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center text-center">
      <p className="font-serif text-6xl font-semibold text-line-strong">404</p>
      <h1 className="mt-4 font-serif text-2xl font-semibold text-ink">Page not found</h1>
      <p className="mt-2 max-w-sm text-sm leading-relaxed text-muted">
        The page you&apos;re looking for doesn&apos;t exist or may have moved.
      </p>
      <div className="mt-6">
        <Button href="/">Back to home</Button>
      </div>
    </div>
  );
}
