from dataclasses import dataclass

@dataclass(frozen=True)
class Capability:
    key: str
    category: str
    label: str
    support: str
    support_basis: str
    gap_note: str
    package_names: tuple[str, ...]
    code_patterns: tuple[str, ...]


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="bundler_workspace",
        category="platform",
        label="Bundler & workspace detection",
        support="detected",
        support_basis="Discovery reads package.json, workspace files, and bundler config files with evidence/confidence.",
        gap_note="Could still expand beyond current React-oriented bundlers/configs.",
        package_names=("vite", "next", "webpack", "expo"),
        code_patterns=("pnpm-workspace.yaml", "turbo.json", "vite.config", "next.config", "webpack.config"),
    ),
    Capability(
        key="react_router_runtime",
        category="routing",
        label="React Router runtime and route objects",
        support="detected",
        support_basis="AST tracks RouterConfig, RouteLoader, RouteAction, RouteLazy, RouteRedirect and router imports.",
        gap_note="Route metadata could still become richer at member/object level.",
        package_names=("react-router-dom", "react-router", "@react-router/dev", "@react-router/node"),
        code_patterns=("createBrowserRouter", "RouterProvider", "loader:", "action:", "redirect(", "RouteObject", "routes.ts", "react-router.config"),
    ),
    Capability(
        key="tanstack_query",
        category="data",
        label="TanStack Query / query client actions",
        support="detected",
        support_basis="AST and state-flow pipeline detect query keys, mutation keys, query client actions, and boundary signals.",
        gap_note="Could still model query option factories and stale/cache semantics more deeply.",
        package_names=("@tanstack/react-query",),
        code_patterns=("useQuery", "useSuspenseQuery", "useMutation", "queryKey:", "mutationKey:", "invalidateQueries", "prefetchQuery", "getQueryData", "getQueryState"),
    ),
    Capability(
        key="redux_toolkit",
        category="state",
        label="Redux Toolkit store/slice/thunk",
        support="detected",
        support_basis="AST now emits ReduxStore, ReduxSlice, and ReduxAsyncThunk signals.",
        gap_note="Selectors and cross-slice relationships are not yet modeled explicitly.",
        package_names=("@reduxjs/toolkit", "react-redux", "redux-persist"),
        code_patterns=("createSlice", "createAsyncThunk", "configureStore", "combineSlices", "persistReducer", "persistStore"),
    ),
    Capability(
        key="zustand",
        category="state",
        label="Zustand stores",
        support="detected",
        support_basis="Nexora SAGE should only mark Zustand when package/import evidence or state-flow store evidence exists.",
        gap_note="Middleware stack semantics could still be richer.",
        package_names=("zustand",),
        code_patterns=("from 'zustand'", 'from "zustand"', "zustand/"),
    ),
    Capability(
        key="context_error_suspense",
        category="react-core",
        label="Context / Provider / Suspense / Error Boundary",
        support="detected",
        support_basis="AST now emits ReactContext, ReactProvider, SuspenseBoundary, and ErrorBoundary features.",
        gap_note="Could still become richer at member-level provider graph semantics.",
        package_names=("react-error-boundary",),
        code_patterns=("createContext", "useContext", ".Provider", "Suspense", "ErrorBoundary"),
    ),
    Capability(
        key="forms_validation",
        category="forms",
        label="Form handling and schema validation",
        support="detected",
        support_basis="AST can emit ReactForm, FormResolver, ValidationSchema, and ZodSchema features from form runtime and schema contracts.",
        gap_note="Could still become richer at field-level validation graph semantics and library-specific resolver semantics.",
        package_names=("react-hook-form", "@hookform/resolvers", "zod", "yup", "formik"),
        code_patterns=("useForm", "zodResolver", "register(", "handleSubmit", "safeParse", ".parse("),
    ),
    Capability(
        key="api_clients_network",
        category="integration",
        label="API clients and network boundaries",
        support="detected",
        support_basis="AST can emit ApiClient, ApiContract, ApiInterceptor, ApiTransport, and ApiResponseContract features from HTTP client wrappers and contract files.",
        gap_note="Could still become richer at repository/service ownership and endpoint graph semantics.",
        package_names=("axios", "graphql-request", "@apollo/client"),
        code_patterns=("axios", "fetch(", "AxiosRequestConfig", "api.service", "api.instance"),
    ),
    Capability(
        key="testing_stack",
        category="tooling",
        label="Testing stack (Jest / Cypress / Playwright / Vitest)",
        support="detected",
        support_basis="AST can emit TestRuntime, JestRuntime, CypressRuntime, PlaywrightRuntime, VitestRuntime, and TestingLibrary features from test files and config entrypoints.",
        gap_note="Could still become richer at fixture/mocking topology and coverage semantics.",
        package_names=("jest", "@jest/globals", "cypress", "playwright", "vitest", "@playwright/test", "@testing-library/react"),
        code_patterns=("jest.config", "cypress.config", "playwright.config", "vitest.config"),
    ),
    Capability(
        key="styling_systems",
        category="ui",
        label="Styling systems (CSS/Sass/Tailwind/UI kits)",
        support="detected",
        support_basis="AST can emit StyleSystem, CssModule, SassModule, UiKit, and DesignToken features from style imports and token usage.",
        gap_note="Could still become richer at theme/provider and utility-class graph semantics.",
        package_names=("tailwindcss", "sass", "styled-components", "@mui/material", "@chakra-ui/react"),
        code_patterns=(".module.scss", ".module.css", "tailwind", "styled(", "@mui", "@chakra-ui"),
    ),
)


CAPABILITY_BACKLOG: dict[str, dict] = {
    "context_error_suspense": {
        "priority": 1,
        "status": "completed",
        "focus": "Context / Provider / Suspense / Error Boundary",
        "ast_work": [
            "Emit ReactContext when createContext/useContext appears via React imports.",
            "Emit ReactProvider from JSX provider tags and .Provider usage.",
            "Emit SuspenseBoundary and ErrorBoundary from imports and JSX tags.",
        ],
        "discovery_work": [
            "No extra discovery work required beyond package/dependency evidence.",
        ],
    },
    "forms_validation": {
        "priority": 2,
        "status": "next",
        "focus": "Form handling and schema validation",
        "ast_work": [
            "Emit ReactForm when useForm/FormProvider/register/handleSubmit are present.",
            "Emit ValidationSchema when zodResolver, safeParse, parse, or schema object composition is present.",
            "Capture member-level form signals for resolver binding and submit handlers.",
        ],
        "discovery_work": [
            "Promote declared package evidence for react-hook-form, @hookform/resolvers, and zod into capability evidence.",
        ],
    },
    "api_clients_network": {
        "priority": 3,
        "status": "next",
        "focus": "API client / contract boundaries",
        "ast_work": [
            "Emit ApiClient when axios.create/fetch wrappers/service factories are present.",
            "Emit ApiContract when request/response contract files or typed client bindings are detected.",
            "Emit ApiInterceptor for axios interceptors and auth/header mutation flows.",
        ],
        "discovery_work": [
            "Promote declared package/config evidence for axios/fetch-centric HTTP clients.",
            "Recognize common api/client/service naming conventions as fallback evidence only.",
        ],
    },
    "testing_stack": {
        "priority": 4,
        "status": "later",
        "focus": "Testing stack",
        "ast_work": [
            "Emit TestRuntime features for Jest/Vitest/Cypress/Playwright imports and globals.",
        ],
        "discovery_work": [
            "Promote declared test-runner config files and devDependencies into capability evidence.",
        ],
    },
    "styling_systems": {
        "priority": 5,
        "status": "later",
        "focus": "Styling systems",
        "ast_work": [
            "Emit StyleSystem and DesignToken signals from CSS modules, Sass modules, and UI-kit imports.",
        ],
        "discovery_work": [
            "Promote declared styling dependencies and config files into capability evidence.",
        ],
    },
}
