"""Request-local current manifest observations, not a runner or Atlas producer."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path, PurePosixPath

from tools.core.agent_command_contracts import target_package_script_command
from tools.core.json_syntax import loads_json_strict
from tools.core.language_registry import language_for_extension
from tools.core.patch_input import PatchInputError, _read_confined, _relative_file
from tools.core.test_impact_profiles import load_test_impact_profiles
from tools.core.target_repository_trust import classify_target_path
from tools.core.unmanaged_atomic_io import native_filesystem_path


def command_source_policy():
    policy=load_test_impact_profiles().get('command_source')
    limits=('max_candidates','max_ancestor_probes','max_manifest_bytes','max_json_nodes','max_json_depth','max_path_characters')
    if (not isinstance(policy,dict) or policy.get('version')!='v1-current-package-test-script'
            or policy.get('script')!='test' or not isinstance(policy.get('package_managers'),list)
            or not policy['package_managers'] or any(not isinstance(m,str) or not m for m in policy['package_managers'])
            or not isinstance(policy.get('source_languages'),list) or not policy['source_languages']
            or any(not isinstance(v,str) or not v for v in policy['source_languages'])
            or any(type(policy.get(k)) is not int or policy[k]<1 for k in limits)
            or not isinstance(policy.get('proof_boundary'),str) or not policy['proof_boundary']):
        raise ValueError('test_command_source_policy_unavailable')
    return policy


def _unavailable(reason):
    return {'status':'unavailable','reason':reason,'execution':'not_run_by_sage'}


class TestCommandSourceObserver:
    """Memoize bounded reads only in one request, then independently recheck."""
    def __init__(self, analysis_root):
        self.policy=deepcopy(command_source_policy())
        self.policy_digest=hashlib.sha256(json.dumps(self.policy,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
        root=Path(analysis_root) if analysis_root else None
        self.analysis_root=root
        self.root=root.resolve() if root is not None and root.is_absolute() else None
        self.sources={}
        self.parsed={}

    def _read(self, relative):
        path=Path(native_filesystem_path(self.root/relative))
        boundary=classify_target_path(self.root,self.root/relative)
        if not boundary['contained'] or boundary['link_path']:
            raise ValueError('manifest_probe_path_refused')
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        return _read_confined(self.root,relative,self.policy['max_manifest_bytes'])

    def _source(self, relative):
        if relative not in self.sources:
            if len(self.sources)>=self.policy['max_candidates']*self.policy['max_ancestor_probes']:
                raise ValueError('manifest_probe_budget_exhausted')
            self.sources[relative]=self._read(relative)
        return self.sources[relative]

    def _package(self, relative, raw):
        if relative not in self.parsed:
            value=loads_json_strict(raw.decode('utf-8-sig'))
            pending,visited=[(value,0)],0
            while pending:
                node,depth=pending.pop()
                visited+=1
                if visited>self.policy['max_json_nodes'] or depth>self.policy['max_json_depth']:
                    raise ValueError('manifest_structure_over_budget')
                if isinstance(node,float) and not math.isfinite(node):
                    raise ValueError('manifest_non_json_number')
                if isinstance(node,dict):
                    pending.extend((v,depth+1) for v in node.values())
                elif isinstance(node,list):
                    pending.extend((v,depth+1) for v in node)
            if not isinstance(value,dict):
                raise ValueError('manifest_not_object')
            self.parsed[relative]=value
        return self.parsed[relative]

    def observe(self, *, project, project_root, candidate_path, atlas_path):
        if self.root is None or not project or not isinstance(project_root,str) or not project_root:
            return _unavailable('candidate_project_root_unavailable')
        try:
            if any(not isinstance(v,str) or len(v)>self.policy['max_path_characters']
                   for v in (project,project_root,candidate_path,atlas_path)):
                return _unavailable('candidate_path_over_budget_or_invalid')
            candidate_path,atlas_path=_relative_file(candidate_path),_relative_file(atlas_path)
            if language_for_extension(PurePosixPath(candidate_path).suffix) not in self.policy['source_languages']:
                return _unavailable('package_test_script_not_applicable_to_language')
            # The selected candidate's two path planes must agree with its own
            # explicit project root; never infer one by stripping a prefix.
            declared=Path(project_root)
            if declared.is_absolute():
                project_rel=declared.relative_to(self.root).as_posix()
            else:
                project_rel=project_root.replace('\\','/')
            if project_rel!='.':
                project_rel=_relative_file(project_rel)
            expected=atlas_path if project_rel=='.' else project_rel+'/'+atlas_path
            if candidate_path!=expected:
                return _unavailable('candidate_project_path_mismatch')
            cursor=PurePosixPath(candidate_path).parent
            boundary=PurePosixPath(project_rel)
            for _ in range(self.policy['max_ancestor_probes']):
                relative=(cursor/'package.json').as_posix()
                raw=self._source(relative)
                if raw is not None:
                    package=self._package(relative,raw)
                    scripts=package.get('scripts')
                    if (not isinstance(scripts,dict) or not isinstance(scripts.get(self.policy['script']),str)
                            or not scripts[self.policy['script']].strip()):
                        return _unavailable('nearest_package_test_script_unavailable')
                    manager=package.get('packageManager')
                    if (not isinstance(manager,str) or '@' not in manager
                            or manager.split('@',1)[0] not in self.policy['package_managers']
                            or not manager.split('@',1)[1].strip()):
                        return _unavailable('declared_package_manager_unavailable')
                    # A fixed '.' is the only interpolated directory. Preserve
                    # the actual directory separately; never build a cd/shell
                    # expression or forward a guessed file argument.
                    command=target_package_script_command(package,'.',self.policy['script'])
                    if not command.startswith(manager.split('@',1)[0]+' '):
                        return _unavailable('package_command_builder_unsupported_manager')
                    return {'status':'observed_current_source','origin':'declared_package_test_script',
                        'project':project,'manifest':relative,'manifest_sha256':hashlib.sha256(raw).hexdigest(),
                        'script':self.policy['script'],'script_sha256':hashlib.sha256(scripts[self.policy['script']].encode('utf-8')).hexdigest(),
                        'package_manager':manager.split('@',1)[0],'working_directory':cursor.as_posix(),
                        'working_directory_base':'analysis_root','command':command,
                        'file_argument_added':False,'execution':'not_run_by_sage',
                        'policy_version':self.policy['version'],'admission_policy_sha256':self.policy_digest,
                        'authority':'current_literal_declaration_only','proof_boundary':self.policy['proof_boundary']}
                if cursor==boundary:
                    return _unavailable('no_package_within_candidate_project')
                cursor=cursor.parent
            return _unavailable('manifest_ancestor_budget_exhausted')
        except (PatchInputError,OSError,RuntimeError,ValueError,UnicodeError,RecursionError) as exc:
            return _unavailable('manifest_observation_refused:'+type(exc).__name__)

    def settle(self, suggestions):
        try:
            stable=(self.analysis_root is not None and self.analysis_root.is_absolute()
                and self.analysis_root.resolve()==self.root and command_source_policy()==self.policy
                and all(self._read(path)==raw for path,raw in self.sources.items()))
        except (PatchInputError,OSError,RuntimeError,ValueError):
            stable=False
        if not stable:
            return [_unavailable('package_source_changed_during_request') for _ in suggestions]
        return suggestions
