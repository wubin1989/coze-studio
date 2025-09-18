package service

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"

	"github.com/bytedance/sonic"
	einoCompose "github.com/cloudwego/eino/compose"
	"github.com/getkin/kin-openapi/openapi3"
	"github.com/tidwall/sjson"

	model "github.com/coze-dev/coze-studio/backend/api/model/crossdomain/plugin"
	common "github.com/coze-dev/coze-studio/backend/api/model/plugin_develop/common"
	"github.com/coze-dev/coze-studio/backend/domain/plugin/entity"
	"github.com/coze-dev/coze-studio/backend/domain/plugin/internal/encoder"
	"github.com/coze-dev/coze-studio/backend/pkg/errorx"
	"github.com/coze-dev/coze-studio/backend/pkg/i18n"
	"github.com/coze-dev/coze-studio/backend/pkg/lang/slices"
	"github.com/coze-dev/coze-studio/backend/pkg/logs"
	"github.com/coze-dev/coze-studio/backend/types/errno"
)

type httpCallImpl struct {
	baseToolCall
	execScene      model.ExecuteScene
	plugin         *entity.PluginInfo
	tool           *entity.ToolInfo
	GetAccessToken func(ctx context.Context, oa *entity.OAuthInfo) (accessToken string, err error)
}

func (h *httpCallImpl) Do(ctx context.Context, args map[string]any) (request string, resp string, err error) {
	httpReq, err := h.buildHTTPRequest(ctx, args)
	if err != nil {
		return "", "", err
	}

	errMsg, err := h.injectAuthInfo(ctx, httpReq)
	if err != nil {
		return "", "", err
	}

	if errMsg != "" {
		event := &model.ToolInterruptEvent{
			Event: model.InterruptEventTypeOfToolNeedOAuth,
			ToolNeedOAuth: &model.ToolNeedOAuthInterruptEvent{
				Message: errMsg,
			},
		}
		return "", "", einoCompose.NewInterruptAndRerunErr(event)
	}

	var reqBodyBytes []byte
	if httpReq.GetBody != nil {
		reqBody, err := httpReq.GetBody()
		if err != nil {
			return "", "", err
		}
		defer reqBody.Close()

		reqBodyBytes, err = io.ReadAll(reqBody)
		if err != nil {
			return "", "", err
		}
	}

	requestStr, err := genRequestString(httpReq, reqBodyBytes)
	if err != nil {
		return "", "", err
	}

	restyReq := defaultHttpCli.NewRequest()
	restyReq.Header = httpReq.Header
	restyReq.Method = httpReq.Method
	restyReq.URL = httpReq.URL.String()
	if reqBodyBytes != nil {
		restyReq.SetBody(reqBodyBytes)
	}
	restyReq.SetContext(ctx)

	logs.CtxDebugf(ctx, "[execute] url=%s, header=%s, method=%s, body=%s",
		restyReq.URL, restyReq.Header, restyReq.Method, restyReq.Body)

	httpResp, err := restyReq.Send()
	if err != nil {
		return "", "", errorx.New(errno.ErrPluginExecuteToolFailed, errorx.KVf(errno.PluginMsgKey, "http request failed, err=%s", err))
	}

	logs.CtxDebugf(ctx, "[execute] status=%s, response=%s", httpResp.Status(), httpResp.String())

	if httpResp.StatusCode() != http.StatusOK {
		return "", "", errorx.New(errno.ErrPluginExecuteToolFailed,
			errorx.KVf(errno.PluginMsgKey, "http request failed, status=%s\nresp=%s", httpResp.Status(), httpResp.String()))
	}

	return requestStr, httpResp.String(), nil
}

func (h *httpCallImpl) buildHTTPRequest(ctx context.Context, argMaps map[string]any) (httpReq *http.Request, err error) {
	tool := h.tool
	rawURL := h.plugin.GetServerURL() + tool.GetSubURL()

	locArgs, err := h.getLocationArguments(ctx, argMaps, tool.Operation.Parameters)
	if err != nil {
		return nil, err
	}

	commonParams := h.plugin.Manifest.CommonParams

	reqURL, err := locArgs.buildHTTPRequestURL(ctx, rawURL, commonParams)
	if err != nil {
		return nil, err
	}

	bodyArgs := map[string]any{}
	for k, v := range argMaps {
		if _, ok := locArgs.header[k]; ok {
			continue
		}
		if _, ok := locArgs.path[k]; ok {
			continue
		}
		if _, ok := locArgs.query[k]; ok {
			continue
		}
		bodyArgs[k] = v
	}

	commonBody := commonParams[model.ParamInBody]
	bodyBytes, contentType, err := h.buildRequestBody(ctx, tool.Operation, bodyArgs, commonBody)
	if err != nil {
		return nil, err
	}

	httpReq, err = http.NewRequestWithContext(ctx, tool.GetMethod(), reqURL.String(), bytes.NewBuffer(bodyBytes))
	if err != nil {
		return nil, err
	}

	commonHeader := commonParams[model.ParamInHeader]
	header, err := locArgs.buildHTTPRequestHeader(ctx, commonHeader)
	if err != nil {
		return nil, err
	}

	httpReq.Header = header

	if len(bodyBytes) > 0 {
		httpReq.Header.Set("Content-Type", contentType)
	}

	return httpReq, nil
}

func (h *httpCallImpl) getLocationArguments(ctx context.Context, args map[string]any, paramRefs []*openapi3.ParameterRef) (*locationArguments, error) {
	headerArgs := map[string]valueWithSchema{}
	pathArgs := map[string]valueWithSchema{}
	queryArgs := map[string]valueWithSchema{}

	for _, paramRef := range paramRefs {
		paramVal := paramRef.Value
		if paramVal.In == openapi3.ParameterInCookie {
			continue
		}

		scVal := paramVal.Schema.Value
		typ := scVal.Type
		if typ == openapi3.TypeObject {
			return nil, fmt.Errorf("the type of '%s' parameter '%s' cannot be 'object'", paramVal.In, paramVal.Name)
		}

		argValue, ok := args[paramVal.Name]
		if !ok {
			var err error
			argValue, err = h.getDefaultValue(ctx, scVal)
			if err != nil {
				return nil, err
			}
			if argValue == nil {
				if !paramVal.Required {
					continue
				}
				return nil, fmt.Errorf("the '%s' parameter '%s' is required", paramVal.In, paramVal.Name)
			}
		}

		v := valueWithSchema{
			argValue:    argValue,
			paramSchema: paramVal,
		}

		switch paramVal.In {
		case openapi3.ParameterInQuery:
			queryArgs[paramVal.Name] = v
		case openapi3.ParameterInHeader:
			headerArgs[paramVal.Name] = v
		case openapi3.ParameterInPath:
			pathArgs[paramVal.Name] = v
		}
	}

	locArgs := &locationArguments{
		header: headerArgs,
		path:   pathArgs,
		query:  queryArgs,
	}

	return locArgs, nil
}

func (h *httpCallImpl) injectAuthInfo(_ context.Context, httpReq *http.Request) (errMsg string, error error) {
	authInfo := h.plugin.GetAuthInfo()
	if authInfo.Type == model.AuthzTypeOfNone {
		return "", nil
	}

	if authInfo.Type == model.AuthzTypeOfService {
		return h.injectServiceAPIToken(httpReq.Context(), httpReq, authInfo)
	}

	if authInfo.Type == model.AuthzTypeOfOAuth {
		return h.injectOAuthAccessToken(httpReq.Context(), httpReq, authInfo)
	}

	return "", nil
}

func genRequestString(req *http.Request, body []byte) (string, error) {
	type Request struct {
		Path   string            `json:"path"`
		Header map[string]string `json:"header"`
		Query  map[string]string `json:"query"`
		Body   *[]byte           `json:"body"`
	}

	req_ := &Request{
		Path:   req.URL.Path,
		Header: map[string]string{},
		Query:  map[string]string{},
	}

	if len(req.Header) > 0 {
		for k, v := range req.Header {
			req_.Header[k] = v[0]
		}
	}
	if len(req.URL.Query()) > 0 {
		for k, v := range req.URL.Query() {
			req_.Query[k] = v[0]
		}
	}

	requestStr, err := sonic.MarshalString(req_)
	if err != nil {
		return "", fmt.Errorf("[genRequestString] marshal failed, err=%s", err)
	}

	if len(body) > 0 {
		requestStr, err = sjson.SetRaw(requestStr, "body", string(body))
		if err != nil {
			return "", fmt.Errorf("[genRequestString] set body failed, err=%s", err)
		}
	}

	return requestStr, nil
}

func (l *locationArguments) buildHTTPRequestURL(_ context.Context, rawURL string,
	commonParams map[model.HTTPParamLocation][]*common.CommonParamSchema) (reqURL *url.URL, err error) {

	if len(l.path) > 0 {
		for k, v := range l.path {
			vStr, err := encoder.EncodeParameter(v.paramSchema, v.argValue)
			if err != nil {
				return nil, err
			}
			rawURL = strings.ReplaceAll(rawURL, "{"+k+"}", vStr)
		}
	}

	query := url.Values{}
	if len(l.query) > 0 {
		for k, val := range l.query {
			switch v := val.argValue.(type) {
			case []any:
				for _, _v := range v {
					query.Add(k, encoder.MustString(_v))
				}
			default:
				query.Add(k, encoder.MustString(v))
			}
		}
	}

	commonQuery := commonParams[model.ParamInQuery]
	for _, v := range commonQuery {
		if _, ok := l.query[v.Name]; ok {
			continue
		}
		query.Add(v.Name, v.Value)
	}

	encodeQuery := query.Encode()

	reqURL, err = url.Parse(rawURL)
	if err != nil {
		return nil, err
	}

	if len(reqURL.RawQuery) > 0 && len(encodeQuery) > 0 {
		reqURL.RawQuery += "&" + encodeQuery
	} else if len(encodeQuery) > 0 {
		reqURL.RawQuery = encodeQuery
	}

	return reqURL, nil
}

func (h *httpCallImpl) buildRequestBody(ctx context.Context, op *model.Openapi3Operation, bodyArgs map[string]any,
	commonBody []*common.CommonParamSchema) (body []byte, contentType string, err error) {

	var bodyMap map[string]any

	contentType, bodySchema := op.GetReqBodySchema()
	if bodySchema != nil && len(bodySchema.Value.Properties) > 0 {
		bodyMap, err = h.injectRequestBodyDefaultValue(ctx, bodySchema.Value, bodyArgs)
		if err != nil {
			return nil, "", err
		}

		for paramName, prop := range bodySchema.Value.Properties {
			value, ok := bodyMap[paramName]
			if !ok {
				continue
			}

			_value, err := encoder.TryCorrectValueType(paramName, prop, value)
			if err != nil {
				return nil, "", err
			}

			bodyMap[paramName] = _value
		}

		body, err = encoder.EncodeBodyWithContentType(contentType, bodyMap)
		if err != nil {
			return nil, "", fmt.Errorf("[buildRequestBody] EncodeBodyWithContentType failed, err=%v", err)
		}
	}

	commonBody_ := make([]*common.CommonParamSchema, 0, len(commonBody))
	for _, v := range commonBody {
		if _, ok := bodyMap[v.Name]; ok {
			continue
		}
		commonBody_ = append(commonBody_, v)
	}

	for _, v := range commonBody_ {
		body, err = sjson.SetRawBytes(body, v.Name, []byte(v.Value))
		if err != nil {
			return nil, "", fmt.Errorf("[buildRequestBody] SetRawBytes failed, err=%v", err)
		}
	}

	return body, contentType, nil
}

func (h *httpCallImpl) injectRequestBodyDefaultValue(ctx context.Context, sc *openapi3.Schema, vals map[string]any) (newVals map[string]any, err error) {
	required := slices.ToMap(sc.Required, func(e string) (string, bool) {
		return e, true
	})

	newVals = make(map[string]any, len(sc.Properties))

	for paramName, prop := range sc.Properties {
		paramSchema := prop.Value
		if paramSchema.Type == openapi3.TypeObject {
			val := vals[paramName]
			if val == nil {
				val = map[string]any{}
			}

			mapVal, ok := val.(map[string]any)
			if !ok {
				return nil, fmt.Errorf("[injectRequestBodyDefaultValue] parameter '%s' is not object", paramName)
			}

			newMapVal, err := h.injectRequestBodyDefaultValue(ctx, paramSchema, mapVal)
			if err != nil {
				return nil, err
			}

			if len(newMapVal) > 0 {
				newVals[paramName] = newMapVal
			}

			continue
		}

		if val := vals[paramName]; val != nil {
			newVals[paramName] = val
			continue
		}

		defaultVal, err := h.getDefaultValue(ctx, paramSchema)
		if err != nil {
			return nil, err
		}
		if defaultVal == nil {
			if !required[paramName] {
				continue
			}
			return nil, fmt.Errorf("[injectRequestBodyDefaultValue] parameter '%s' is required", paramName)
		}

		newVals[paramName] = defaultVal
	}

	return newVals, nil
}

func (h *httpCallImpl) injectServiceAPIToken(ctx context.Context, httpReq *http.Request, authInfo *model.AuthV2) (errMsg string, err error) {
	if authInfo.SubType == model.AuthzSubTypeOfServiceAPIToken {
		authOfAPIToken := authInfo.AuthOfAPIToken
		if authOfAPIToken == nil {
			return "", fmt.Errorf("auth of api token is nil")
		}

		loc := strings.ToLower(string(authOfAPIToken.Location))
		if loc == openapi3.ParameterInQuery {
			query := httpReq.URL.Query()
			if query.Get(authOfAPIToken.Key) == "" {
				query.Set(authOfAPIToken.Key, authOfAPIToken.ServiceToken)
				httpReq.URL.RawQuery = query.Encode()
			}
		}

		if loc == openapi3.ParameterInHeader {
			if httpReq.Header.Get(authOfAPIToken.Key) == "" {
				httpReq.Header.Set(authOfAPIToken.Key, authOfAPIToken.ServiceToken)
			}
		}
	}

	return "", nil
}

func (h *httpCallImpl) injectOAuthAccessToken(ctx context.Context, httpReq *http.Request, authInfo *model.AuthV2) (errMsg string, err error) {
	authMode := model.ToolAuthModeOfRequired
	if tmp, ok := h.tool.Operation.Extensions[model.APISchemaExtendAuthMode].(string); ok {
		authMode = model.ToolAuthMode(tmp)
	}

	if authMode == model.ToolAuthModeOfDisabled {
		return "", nil
	}

	var accessToken string

	if authInfo.SubType == model.AuthzSubTypeOfOAuthAuthorizationCode {
		i := &entity.AuthorizationCodeInfo{
			Meta: &entity.AuthorizationCodeMeta{
				UserID:   h.userID,
				PluginID: h.plugin.ID,
				IsDraft:  h.execScene == model.ExecSceneOfToolDebug,
			},
			Config: authInfo.AuthOfOAuthAuthorizationCode,
		}

		accessToken, err = h.GetAccessToken(ctx, &entity.OAuthInfo{
			OAuthMode:         authInfo.SubType,
			AuthorizationCode: i,
		})
		if err != nil {
			return "", err
		}

		if accessToken == "" && authMode != model.ToolAuthModeOfSupported {
			errMsg = authCodeInvalidTokenErrMsg[i18n.GetLocale(ctx)]
			if errMsg == "" {
				errMsg = authCodeInvalidTokenErrMsg[i18n.LocaleEN]
			}
			authURL, err := genAuthURL(i)
			if err != nil {
				return "", err
			}

			errMsg = fmt.Sprintf(errMsg, h.plugin.Manifest.NameForHuman, authURL)

			return errMsg, nil
		}
	}

	if accessToken != "" {
		httpReq.Header.Set("Authorization", fmt.Sprintf("Bearer %s", accessToken))
	}

	return "", nil
}

var authCodeInvalidTokenErrMsg = map[i18n.Locale]string{
	i18n.LocaleZH: "%s 插件需要授权使用。授权后即代表你同意与扣子中你所选择的 AI 模型分享数据。请[点击这里](%s)进行授权。",
	i18n.LocaleEN: "The '%s' plugin requires authorization. By authorizing, you agree to share data with the AI model you selected in Coze. Please [click here](%s) to authorize.",
}

type locationArguments struct {
	header map[string]valueWithSchema
	path   map[string]valueWithSchema
	query  map[string]valueWithSchema
}

type valueWithSchema struct {
	argValue    any
	paramSchema *openapi3.Parameter
}

func (l *locationArguments) buildHTTPRequestHeader(_ context.Context, commonHeaders []*common.CommonParamSchema) (http.Header, error) {
	header := http.Header{}
	if len(l.header) > 0 {
		for k, v := range l.header {
			switch vv := v.argValue.(type) {
			case []any:
				for _, _v := range vv {
					header.Add(k, encoder.MustString(_v))
				}
			default:
				header.Add(k, encoder.MustString(vv))
			}
		}
	}

	for _, h := range commonHeaders {
		if header.Get(h.Name) != "" {
			continue
		}
		header.Add(h.Name, h.Value)
	}

	return header, nil
}
