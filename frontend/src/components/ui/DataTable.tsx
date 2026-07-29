import type { ReactNode } from "react";
import { EmptyState } from "../common";

interface Column<T> {
  key: string;
  header: ReactNode;
  width?: string | number;
  align?: "left" | "center" | "right";
  render?: (row: T, index: number) => ReactNode;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  keyExtractor: (row: T) => string;
  empty?: { text: string; hint?: string; action?: ReactNode };
  loading?: boolean;
  onRowClick?: (row: T) => void;
  className?: string;
  "data-testid"?: string;
  rowDataTestId?: (row: T) => string | undefined;
}

export function DataTable<T>({
  columns,
  rows,
  keyExtractor,
  empty,
  loading,
  onRowClick,
  className = "",
  "data-testid": testId,
  rowDataTestId,
}: DataTableProps<T>) {
  if (!loading && rows.length === 0) {
    return (
      <EmptyState
        text={empty?.text ?? "暂无数据"}
        hint={empty?.hint}
        action={empty?.action}
      />
    );
  }

  return (
    <div className="data-table-scroll" data-testid={testId}>
      <table className={`tbl data-table ${className}`}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                className={col.align ? `text-${col.align}` : "text-left"}
                style={{ width: col.width }}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={keyExtractor(row)}
              data-testid={rowDataTestId?.(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={onRowClick ? "cursor-pointer" : undefined}
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={col.align ? `text-${col.align}` : "text-left"}
                  style={{ width: col.width }}
                >
                  {col.render ? col.render(row, i) : null}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
