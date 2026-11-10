-- public 스키마의 테이블·컬럼·제약·인덱스·뷰·enum 타입을 JSON 문서 하나로 조회한다.
-- oid 와 attnum(ordinal)은 같은 DB 안에서 이름이 바뀌어도 유지되므로, 이력 재생 시 이름 변경 판정에 쓴다.
WITH t AS (
    SELECT c.oid, c.relname AS name, obj_description(c.oid, 'pg_class') AS comment,
           CASE WHEN c.relkind = 'p' THEN pg_get_partkeydef(c.oid) END AS partition_by
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND NOT c.relispartition
      AND c.relname NOT IN ('flyway_schema_history', 'databasechangelog', 'databasechangeloglock', 'alembic_version', '_prisma_migrations')
      AND NOT EXISTS (SELECT 1 FROM pg_depend dep WHERE dep.classid = 'pg_class'::regclass AND dep.objid = c.oid AND dep.deptype = 'e')
)
SELECT json_build_object(
    'tables', COALESCE((
        SELECT json_agg(json_build_object(
            'name', t.name,
            'oid', t.oid::bigint,
            'comment', t.comment,
            'partition_by', t.partition_by,
            'columns', COALESCE((
                SELECT json_agg(json_build_object(
                    'name', a.attname,
                    'ordinal', a.attnum,
                    'type', format_type(a.atttypid, a.atttypmod),
                    'nullable', NOT a.attnotnull,
                    'identity', NULLIF(a.attidentity::text, ''),
                    'generated', NULLIF(a.attgenerated::text, ''),
                    'default', pg_get_expr(d.adbin, d.adrelid),
                    'owned_sequence', pg_get_serial_sequence(quote_ident(t.name), a.attname),
                    'collation', CASE WHEN a.attcollation <> 0 AND a.attcollation <> ty.typcollation
                                      THEN (SELECT quote_ident(cl.collname) FROM pg_collation cl WHERE cl.oid = a.attcollation) END,
                    'comment', col_description(t.oid, a.attnum)
                ) ORDER BY a.attnum)
                FROM pg_attribute a
                JOIN pg_type ty ON ty.oid = a.atttypid
                LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                WHERE a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
            ), '[]'::json),
            'constraints', COALESCE((
                SELECT json_agg(json_build_object(
                    'name', k.conname,
                    'oid', k.oid::bigint,
                    'kind', k.contype,
                    'columns', (
                        SELECT json_agg(a.attname ORDER BY u.ord)
                        FROM unnest(k.conkey) WITH ORDINALITY u(attnum, ord)
                        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.attnum
                    ),
                    'ref_table', CASE WHEN k.contype = 'f' THEN (
                        SELECT CASE WHEN rn.nspname = 'public' THEN rc.relname ELSE rn.nspname || '.' || rc.relname END
                        FROM pg_class rc JOIN pg_namespace rn ON rn.oid = rc.relnamespace WHERE rc.oid = k.confrelid
                    ) END,
                    'ref_schema', CASE WHEN k.contype = 'f' THEN (
                        SELECT NULLIF(rn.nspname, 'public') FROM pg_class rc JOIN pg_namespace rn ON rn.oid = rc.relnamespace WHERE rc.oid = k.confrelid
                    ) END,
                    'ref_columns', CASE WHEN k.contype = 'f' THEN (
                        SELECT json_agg(a.attname ORDER BY u.ord)
                        FROM unnest(k.confkey) WITH ORDINALITY u(attnum, ord)
                        JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.attnum
                    ) END,
                    'definition', pg_get_constraintdef(k.oid)
                ) ORDER BY k.conname)
                FROM pg_constraint k
                WHERE k.conrelid = t.oid
            ), '[]'::json),
            'indexes', COALESCE((
                SELECT json_agg(json_build_object(
                    'name', ic.relname,
                    'oid', i.indexrelid::bigint,
                    'unique', i.indisunique,
                    'columns', (
                        SELECT json_agg(a.attname ORDER BY u.ord)
                        FROM unnest(i.indkey::int2[]) WITH ORDINALITY u(attnum, ord)
                        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = u.attnum
                    ),
                    'definition', pg_get_indexdef(i.indexrelid)
                ) ORDER BY ic.relname)
                FROM pg_index i
                JOIN pg_class ic ON ic.oid = i.indexrelid
                WHERE i.indrelid = t.oid
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_constraint k
                      WHERE k.conindid = i.indexrelid AND k.conrelid = t.oid
                  )
            ), '[]'::json)
        ) ORDER BY t.name)
        FROM t
    ), '[]'::json),
    'views', COALESCE((
        SELECT json_agg(json_build_object(
            'name', v.viewname, 'definition', v.definition,
            'depends', COALESCE((
                SELECT json_agg(DISTINCT dc.relname ORDER BY dc.relname)
                FROM pg_rewrite rw
                JOIN pg_depend dd ON dd.classid = 'pg_rewrite'::regclass AND dd.objid = rw.oid AND dd.refclassid = 'pg_class'::regclass
                JOIN pg_class dc ON dc.oid = dd.refobjid
                WHERE rw.ev_class = format('%I.%I', v.schemaname, v.viewname)::regclass AND dc.oid <> rw.ev_class
            ), '[]'::json)
        ) ORDER BY v.viewname)
        FROM pg_views v
        WHERE v.schemaname = 'public'
          AND NOT EXISTS (SELECT 1 FROM pg_depend dep
                          WHERE dep.classid = 'pg_class'::regclass AND dep.deptype = 'e'
                            AND dep.objid = format('%I.%I', v.schemaname, v.viewname)::regclass)
    ), '[]'::json),
    'types', COALESCE((
        SELECT json_agg(json_build_object(
            'name', ty.typname, 'kind', 'enum',
            'labels', (SELECT json_agg(e.enumlabel ORDER BY e.enumsortorder) FROM pg_enum e WHERE e.enumtypid = ty.oid)
        ) ORDER BY ty.typname)
        FROM pg_type ty
        JOIN pg_namespace n ON n.oid = ty.typnamespace
        WHERE n.nspname = 'public' AND ty.typtype = 'e'
          AND NOT EXISTS (SELECT 1 FROM pg_depend dep WHERE dep.classid = 'pg_type'::regclass AND dep.objid = ty.oid AND dep.deptype = 'e')
    ), '[]'::json)
);
