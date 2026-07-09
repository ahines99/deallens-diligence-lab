import type { ReactNode } from "react";

type Align = "left" | "right" | "center";

function alignClass(align?: Align): string {
  if (align === "right") return "text-right";
  if (align === "center") return "text-center";
  return "text-left";
}

export function Table({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className={`w-full border-collapse text-sm ${className}`}>{children}</table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return (
    <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
      {children}
    </thead>
  );
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody className="divide-y divide-slate-100">{children}</tbody>;
}

export function TR({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <tr className={className}>{children}</tr>;
}

export function TH({
  children,
  className = "",
  align,
  colSpan,
}: {
  children?: ReactNode;
  className?: string;
  align?: Align;
  colSpan?: number;
}) {
  return (
    <th colSpan={colSpan} className={`px-4 py-2.5 font-medium ${alignClass(align)} ${className}`}>
      {children}
    </th>
  );
}

export function TD({
  children,
  className = "",
  align,
  colSpan,
}: {
  children?: ReactNode;
  className?: string;
  align?: Align;
  colSpan?: number;
}) {
  return (
    <td colSpan={colSpan} className={`px-4 py-2.5 align-top text-slate-700 ${alignClass(align)} ${className}`}>
      {children}
    </td>
  );
}

export interface Column<T> {
  key: string;
  header: ReactNode;
  align?: Align;
  render: (row: T) => ReactNode;
}

export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  empty = "No rows yet.",
}: {
  columns: Column<T>[];
  rows: T[];
  getRowKey?: (row: T, i: number) => string;
  empty?: ReactNode;
}) {
  return (
    <Table>
      <THead>
        <TR>
          {columns.map((c) => (
            <TH key={c.key} align={c.align}>
              {c.header}
            </TH>
          ))}
        </TR>
      </THead>
      <TBody>
        {rows.length === 0 ? (
          <TR>
            <TD colSpan={columns.length} align="center" className="py-8 text-slate-400">
              {empty}
            </TD>
          </TR>
        ) : (
          rows.map((row, i) => (
            <TR key={getRowKey ? getRowKey(row, i) : i} className="hover:bg-slate-50">
              {columns.map((c) => (
                <TD key={c.key} align={c.align}>
                  {c.render(row)}
                </TD>
              ))}
            </TR>
          ))
        )}
      </TBody>
    </Table>
  );
}
