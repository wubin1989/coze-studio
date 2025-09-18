package service

import (
	"context"
	"fmt"
	"strconv"

	"github.com/getkin/kin-openapi/openapi3"
	"github.com/go-resty/resty/v2"

	model "github.com/coze-dev/coze-studio/backend/api/model/crossdomain/plugin"
	"github.com/coze-dev/coze-studio/backend/api/model/crossdomain/variables"
	"github.com/coze-dev/coze-studio/backend/api/model/data/variable/project_memory"
	crossvariables "github.com/coze-dev/coze-studio/backend/crossdomain/contract/variables"
	"github.com/coze-dev/coze-studio/backend/domain/plugin/entity"
	"github.com/coze-dev/coze-studio/backend/pkg/lang/ptr"
	"github.com/coze-dev/coze-studio/backend/pkg/logs"
)

var defaultHttpCli *resty.Client = resty.New()

type tollCall interface {
	Do(ctx context.Context, args map[string]any) (request string, resp string, err error)
}

type baseToolCall struct {
	projectInfo *entity.ProjectInfo
	userID      string
}

func (b *baseToolCall) getDefaultValue(ctx context.Context, scVal *openapi3.Schema) (any, error) {
	vn, exist := scVal.Extensions[model.APISchemaExtendVariableRef]
	if !exist {
		return scVal.Default, nil
	}

	vnStr, ok := vn.(string)
	if !ok {
		logs.CtxErrorf(ctx, "invalid variable_ref type '%T'", vn)
		return nil, nil
	}

	variableVal, err := b.getVariableValue(ctx, vnStr)
	if err != nil {
		return nil, err
	}

	return variableVal, nil
}

func (b *baseToolCall) getVariableValue(ctx context.Context, keyword string) (any, error) {
	info := b.projectInfo
	if info == nil {
		return nil, fmt.Errorf("project info is nil")
	}

	meta := &variables.UserVariableMeta{
		BizType:      project_memory.VariableConnector_Bot,
		BizID:        strconv.FormatInt(info.ProjectID, 10),
		Version:      ptr.FromOrDefault(info.ProjectVersion, ""),
		ConnectorUID: b.userID,
		ConnectorID:  info.ConnectorID,
	}
	vals, err := crossvariables.DefaultSVC().GetVariableInstance(ctx, meta, []string{keyword})
	if err != nil {
		return nil, err
	}

	if len(vals) == 0 {
		return nil, nil
	}

	return vals[0].Value, nil
}
