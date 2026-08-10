import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import ts from 'typescript';

const root = process.cwd();
const apiPath = path.join(root, 'src', 'renderer', 'lib', 'api.ts');
const ownershipPath = path.join(
  root,
  'contracts',
  'strict-ui-domain-ownership.v1.json',
);

function expressionName(node, sourceFile) {
  return node.getText(sourceFile).replace(/[^A-Za-z0-9_]+/g, '_') || 'value';
}

function staticText(node, sourceFile) {
  if (
    ts.isStringLiteral(node)
    || ts.isNoSubstitutionTemplateLiteral(node)
  ) {
    return node.text;
  }
  if (ts.isTemplateExpression(node)) {
    let value = node.head.text;
    for (const span of node.templateSpans) {
      const name = expressionName(span.expression, sourceFile);
      if (
        !/(?:query|suffix|params|search)/i.test(name)
        && !/^q?s(?:_.*)?$/i.test(name)
        && !/^q$/i.test(name)
      ) {
        value += `:${name}`;
      }
      value += span.literal.text;
    }
    return value;
  }
  if (
    ts.isBinaryExpression(node)
    && node.operatorToken.kind === ts.SyntaxKind.PlusToken
  ) {
    const left = staticText(node.left, sourceFile);
    const right = staticText(node.right, sourceFile);
    return left !== null && right !== null ? left + right : null;
  }
  if (ts.isConditionalExpression(node)) {
    const whenTrue = staticText(node.whenTrue, sourceFile);
    const whenFalse = staticText(node.whenFalse, sourceFile);
    if (whenTrue !== null && whenFalse !== null) {
      const truePath = whenTrue.split('?')[0];
      const falsePath = whenFalse.split('?')[0];
      return truePath === falsePath ? whenFalse : null;
    }
  }
  return null;
}

function containingFunctionName(node) {
  let current = node;
  while (current) {
    if (ts.isFunctionDeclaration(current) && current.name) {
      return current.name.text;
    }
    if (
      (ts.isArrowFunction(current) || ts.isFunctionExpression(current))
      && current.parent
      && ts.isVariableDeclaration(current.parent)
      && ts.isIdentifier(current.parent.name)
    ) {
      return current.parent.name.text;
    }
    current = current.parent;
  }
  return 'module';
}

function resolveLocalInitializer(identifier, call, sourceFile) {
  let scope = call;
  while (
    scope
    && !ts.isFunctionDeclaration(scope)
    && !ts.isFunctionExpression(scope)
    && !ts.isArrowFunction(scope)
  ) {
    scope = scope.parent;
  }
  if (!scope) return null;
  let initializer = null;
  function visit(node) {
    if (node.getStart(sourceFile) >= call.getStart(sourceFile)) return;
    if (
      ts.isVariableDeclaration(node)
      && ts.isIdentifier(node.name)
      && node.name.text === identifier.text
      && node.initializer
    ) {
      initializer = node.initializer;
    }
    ts.forEachChild(node, visit);
  }
  visit(scope);
  return initializer;
}

function requestMethod(call, calleeName) {
  const defaultMethod = calleeName === 'requestForm' ? 'POST' : 'GET';
  for (const argument of [...call.arguments].reverse()) {
    if (!ts.isObjectLiteralExpression(argument)) continue;
    for (const property of argument.properties) {
      if (
        ts.isPropertyAssignment(property)
        && property.name.getText().replaceAll(/['"]/g, '') === 'method'
        && ts.isStringLiteral(property.initializer)
      ) {
        return property.initializer.text.toUpperCase();
      }
    }
  }
  return defaultMethod;
}

function normalizePath(value) {
  const prefix = '/api/v2/ui/';
  if (!value.startsWith(prefix)) return null;
  return value
    .slice(prefix.length)
    .split('?')[0]
    .replace(/\/+/g, '/')
    .replace(/^\/|\/$/g, '');
}

function extractRoutes() {
  const source = fs.readFileSync(apiPath, 'utf8');
  const sourceFile = ts.createSourceFile(
    apiPath,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const routes = [];
  const unresolved = [];

  function visit(node) {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
      const callee = node.expression.text;
      if (callee === 'request' || callee === 'requestForm') {
        const pathArgument = node.arguments[0];
        let raw = pathArgument
          ? staticText(pathArgument, sourceFile)
          : null;
        if (raw === null && pathArgument && ts.isIdentifier(pathArgument)) {
          const initializer = resolveLocalInitializer(
            pathArgument,
            node,
            sourceFile,
          );
          raw = initializer ? staticText(initializer, sourceFile) : null;
        }
        const routePath = raw ? normalizePath(raw) : null;
        const line = sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1;
        if (routePath) {
          routes.push({
            method: requestMethod(node, callee),
            path: routePath,
            function: containingFunctionName(node),
            line,
          });
        } else if (raw === null) {
          unresolved.push({
            function: containingFunctionName(node),
            line,
            expression: node.arguments[0]?.getText(sourceFile) ?? '',
          });
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);

  const unique = new Map();
  for (const route of routes) {
    unique.set(`${route.method} ${route.path}`, route);
  }
  return {
    routes: [...unique.values()].sort((a, b) => (
      a.path.localeCompare(b.path) || a.method.localeCompare(b.method)
    )),
    unresolved,
  };
}

function loadOwnership() {
  const raw = JSON.parse(fs.readFileSync(ownershipPath, 'utf8'));
  return raw.domains.map((domain) => ({
    ...domain,
    regexes: domain.patterns.map((pattern) => new RegExp(pattern)),
  }));
}

function assignRoutes(routes, domains) {
  return routes.map((route) => {
    const owner = domains.find((domain) => (
      domain.regexes.some((pattern) => pattern.test(route.path))
    ));
    return {...route, domain: owner?.id ?? null};
  });
}

const domainArgIndex = process.argv.indexOf('--domain');
const selectedDomain = domainArgIndex >= 0
  ? process.argv[domainArgIndex + 1]
  : null;
const asJson = process.argv.includes('--json');
const extracted = extractRoutes();
const routes = assignRoutes(extracted.routes, loadOwnership());
const visible = selectedDomain
  ? routes.filter((route) => route.domain === selectedDomain)
  : routes;
const unassigned = routes.filter((route) => route.domain === null);

if (asJson) {
  process.stdout.write(`${JSON.stringify(visible, null, 2)}\n`);
} else {
  const counts = new Map();
  for (const route of routes) {
    const key = route.domain ?? 'UNASSIGNED';
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  console.log(`UI route operations: ${routes.length}`);
  for (const [domain, count] of [...counts].sort()) {
    console.log(`${domain}: ${count}`);
  }
  console.log(`dynamic request expressions: ${extracted.unresolved.length}`);
  for (const item of extracted.unresolved) {
    console.log(
      `DYNAMIC ${item.function}:${item.line} ${item.expression}`,
    );
  }
  if (selectedDomain) {
    console.log('');
    for (const route of visible) {
      console.log(
        `${route.method.padEnd(6)} ${route.path}  ${route.function}:${route.line}`,
      );
    }
  }
}

if (unassigned.length > 0) {
  console.error('');
  console.error(`Unassigned UI routes: ${unassigned.length}`);
  for (const route of unassigned) {
    console.error(`${route.method} ${route.path}`);
  }
  process.exitCode = 1;
}
