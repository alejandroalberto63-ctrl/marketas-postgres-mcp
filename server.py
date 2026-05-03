import json
import os
from typing import Optional
import psycopg2
import psycopg2.extras
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

# ── Config ──────────────────────────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "marketa_realstatepostgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "marketa_realstate")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASS = os.getenv("DB_PASS", "")          # set via env var en Easypanel
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
API_KEY  = os.getenv("API_KEY", "")         # opcional: protege el endpoint

mcp = FastMCP("marketas_postgres_mcp")

# ── Helpers ──────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
        connect_timeout=10
    )

def run_query(sql: str, params=None) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

def safe_json(data) -> str:
    return json.dumps(data, default=str, ensure_ascii=False, indent=2)

# ── Input Models ─────────────────────────────────────────────────────────────
class QueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sql: str = Field(..., description="Consulta SQL SELECT. No se permiten INSERT/UPDATE/DELETE/DROP.")
    limit: Optional[int] = Field(default=100, ge=1, le=2000,
                                  description="Límite máximo de filas (default 100, max 2000)")

class TableInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    table_name: str = Field(..., description="Nombre exacto de la tabla", min_length=1, max_length=100)

class SearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sector: Optional[str] = Field(default=None, description="Sector/barrio a filtrar")
    tipo: Optional[str] = Field(default=None, description="Tipo de propiedad: 'departamento', 'casa', 'oficina', etc.")
    precio_min: Optional[float] = Field(default=None, description="Precio mínimo en USD")
    precio_max: Optional[float] = Field(default=None, description="Precio máximo en USD")
    m2_min: Optional[float] = Field(default=None, description="Metros cuadrados mínimos")
    m2_max: Optional[float] = Field(default=None, description="Metros cuadrados máximos")
    habitaciones: Optional[int] = Field(default=None, description="Número de habitaciones")
    limit: Optional[int] = Field(default=50, ge=1, le=500, description="Máximo de resultados")

# ── Tools ────────────────────────────────────────────────────────────────────
@mcp.tool(
    name="pg_query",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                  "idempotentHint": True, "openWorldHint": False}
)
async def pg_query(params: QueryInput) -> str:
    """Ejecuta una consulta SQL SELECT sobre marketa_realstate y retorna los resultados en JSON.

    Solo permite sentencias SELECT. Se aplica LIMIT automáticamente si no está en la query.
    Útil para análisis ad-hoc, joins complejos, y consultas personalizadas.

    Args:
        params.sql   (str): Query SELECT válida
        params.limit (int): Límite de filas (default 100)

    Returns:
        str: JSON con lista de filas como objetos {"campo": valor}
    """
    sql = params.sql.strip()
    forbidden = ["insert", "update", "delete", "drop", "truncate", "alter", "create", "grant"]
    if any(sql.lower().startswith(k) for k in forbidden):
        return json.dumps({"error": "Solo se permiten consultas SELECT."})

    # Inyectar LIMIT si no tiene
    sql_lower = sql.lower()
    if "limit" not in sql_lower:
        sql = sql.rstrip(";") + f" LIMIT {params.limit}"

    try:
        rows = run_query(sql)
        return safe_json({"count": len(rows), "rows": rows})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    name="pg_list_tables",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                  "idempotentHint": True, "openWorldHint": False}
)
async def pg_list_tables() -> str:
    """Lista todas las tablas y vistas disponibles en la base de datos marketa_realstate,
    incluyendo nombre, tipo (tabla/vista), y número estimado de filas.

    Returns:
        str: JSON con lista de tablas {table_name, table_type, row_estimate}
    """
    sql = """
        SELECT
            t.table_name,
            t.table_type,
            COALESCE(s.n_live_tup, 0) AS row_estimate
        FROM information_schema.tables t
        LEFT JOIN pg_stat_user_tables s ON s.relname = t.table_name
        WHERE t.table_schema = 'public'
        ORDER BY t.table_type, t.table_name
    """
    try:
        rows = run_query(sql)
        return safe_json({"tables": rows})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    name="pg_describe_table",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                  "idempotentHint": True, "openWorldHint": False}
)
async def pg_describe_table(params: TableInput) -> str:
    """Describe la estructura de una tabla: columnas, tipos, nullability y comentarios.

    Args:
        params.table_name (str): Nombre exacto de la tabla

    Returns:
        str: JSON con columnas {column_name, data_type, is_nullable, column_default, description}
    """
    sql = """
        SELECT
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.column_default,
            pgd.description
        FROM information_schema.columns c
        LEFT JOIN pg_catalog.pg_statio_all_tables st
            ON st.schemaname = c.table_schema AND st.relname = c.table_name
        LEFT JOIN pg_catalog.pg_description pgd
            ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
        WHERE c.table_schema = 'public' AND c.table_name = %s
        ORDER BY c.ordinal_position
    """
    try:
        rows = run_query(sql, (params.table_name,))
        if not rows:
            return json.dumps({"error": f"Tabla '{params.table_name}' no encontrada."})
        return safe_json({"table": params.table_name, "columns": rows})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    name="pg_search_propiedades",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                  "idempotentHint": True, "openWorldHint": False}
)
async def pg_search_propiedades(params: SearchInput) -> str:
    """Busca propiedades en plus_propiedades con filtros de sector, tipo, precio y m².
    Incluye precio_m2 calculado. Ideal para análisis de mercado y reportes.

    Args:
        params.sector      (str):   Filtro parcial de sector/barrio (ILIKE)
        params.tipo        (str):   Tipo de propiedad (ILIKE)
        params.precio_min  (float): Precio mínimo USD
        params.precio_max  (float): Precio máximo USD
        params.m2_min      (float): m² mínimos
        params.m2_max      (float): m² máximos
        params.habitaciones(int):   Número exacto de habitaciones
        params.limit       (int):   Máximo resultados (default 50)

    Returns:
        str: JSON con propiedades + estadísticas básicas (avg precio_m2, min/max precio)
    """
    conditions = ["p.activo = true"]
    args = []

    if params.sector:
        conditions.append("p.sector ILIKE %s")
        args.append(f"%{params.sector}%")
    if params.tipo:
        conditions.append("p.tipo ILIKE %s")
        args.append(f"%{params.tipo}%")
    if params.precio_min is not None:
        conditions.append("p.precio >= %s")
        args.append(params.precio_min)
    if params.precio_max is not None:
        conditions.append("p.precio <= %s")
        args.append(params.precio_max)
    if params.m2_min is not None:
        conditions.append("p.m2_totales >= %s")
        args.append(params.m2_min)
    if params.m2_max is not None:
        conditions.append("p.m2_totales <= %s")
        args.append(params.m2_max)
    if params.habitaciones is not None:
        conditions.append("p.habitaciones = %s")
        args.append(params.habitaciones)

    where = " AND ".join(conditions)
    sql = f"""
        SELECT
            p.*,
            CASE WHEN p.m2_totales > 0 THEN ROUND((p.precio / p.m2_totales)::numeric, 2) END AS precio_m2
        FROM plus_propiedades p
        WHERE {where}
        ORDER BY p.precio_m2 ASC NULLS LAST
        LIMIT {params.limit}
    """
    # Stats query
    sql_stats = f"""
        SELECT
            COUNT(*)                                              AS total,
            ROUND(AVG(p.precio / NULLIF(p.m2_totales,0))::numeric,2) AS avg_precio_m2,
            MIN(p.precio)                                         AS precio_min,
            MAX(p.precio)                                         AS precio_max,
            ROUND(AVG(p.m2_totales)::numeric,1)                  AS avg_m2
        FROM plus_propiedades p
        WHERE {where}
    """
    try:
        rows  = run_query(sql, args)
        stats = run_query(sql_stats, args)
        return safe_json({
            "stats": stats[0] if stats else {},
            "count": len(rows),
            "propiedades": rows
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    name="pg_precio_m2_por_sector",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                  "idempotentHint": True, "openWorldHint": False}
)
async def pg_precio_m2_por_sector() -> str:
    """Reporte de precio por m² agrupado por sector y tipo de propiedad.
    Usado para análisis de mercado y generación de reportes Marketas Realty.

    Returns:
        str: JSON con {sector, tipo, avg_precio_m2, min_precio_m2, max_precio_m2, total_propiedades}
             ordenado por sector y tipo.
    """
    sql = """
        SELECT
            sector,
            tipo,
            COUNT(*)                                                    AS total,
            ROUND(AVG(precio / NULLIF(m2_totales,0))::numeric, 2)      AS avg_precio_m2,
            ROUND(MIN(precio / NULLIF(m2_totales,0))::numeric, 2)      AS min_precio_m2,
            ROUND(MAX(precio / NULLIF(m2_totales,0))::numeric, 2)      AS max_precio_m2,
            ROUND(AVG(precio)::numeric, 0)                              AS avg_precio,
            ROUND(AVG(m2_totales)::numeric, 1)                         AS avg_m2
        FROM plus_propiedades
        WHERE activo = true
          AND m2_totales > 0
          AND precio > 0
        GROUP BY sector, tipo
        HAVING COUNT(*) >= 3
        ORDER BY sector, avg_precio_m2 DESC
    """
    try:
        rows = run_query(sql)
        return safe_json({"sectores": rows, "total_grupos": len(rows)})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=MCP_PORT)
