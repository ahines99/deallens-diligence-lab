import type { ReactNode } from "react";

type Align = "left" | "right" | "center";
const alignCls = (a?: Align) =>
  a === "right" ? "text-right" : a === "center" ? "text-center" : "text-left";

export function Table({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className="-mx-5 overflow-x-auto px-5">
      <table className={`w-full border-collapse text-sm ${className}`}>{children}</table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead>{children}</thead>;
}
export function TBody({ children }: { children: ReactNode }) {
  return <tbody>{children}</tbody>;
}
export function TR({ children, className = "", id }: { children: ReactNode; className?: string; id?: string }) {
  return (
    <tr id={id} className={`border-b border-line-faint last:border-0 ${className}`}>
      {children}
    </tr>
  );
}
export function TH({
  children,
  align,
  className = "",
}: {
  children?: ReactNode;
  align?: Align;
  className?: string;
}) {
  return (
    <th
      className={`whitespace-nowrap border-b-[1.5px] border-ink/80 py-2 pr-4 text-2xs font-semibold uppercase tracking-eyebrow text-muted ${alignCls(align)} ${className}`}
    >
      {children}
    </th>
  );
}
export function TD({
  children,
  align,
  className = "",
}: {
  children?: ReactNode;
  align?: Align;
  className?: string;
}) {
  return (
    <td className={`py-2.5 pr-4 align-top text-[0.82rem] text-body ${alignCls(align)} ${className}`}>
      {children}
    </td>
  );
}

export interface Column<T> {
  key: string;
  header: ReactNode;
  align?: Align;
  render: (row: T) => ReactNode;
  className?: string;
}

export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  empty = "No rows.",
  rowId,
}: {
  columns: Column<T>[];
  rows: T[];
  getRowKey?: (row: T, i: number) => string;
  empty?: ReactNode;
  rowId?: (row: T, i: number) => string | undefined;
}) {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted">{empty}</p>;
  }
  return (
    <Table>
      <THead>
        <TR className="border-0">
          {columns.map((c) => (
            <TH key={c.key} align={c.align}>
              {c.header}
            </TH>
          ))}
        </TR>
      </THead>
      <TBody>
        {rows.map((row, i) => (
          <TR key={getRowKey ? getRowKey(row, i) : i} id={rowId?.(row, i)}>
            {columns.map((c) => (
              <TD key={c.key} align={c.align} className={c.className}>
                {c.render(row)}
              </TD>
            ))}
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
