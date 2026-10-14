#!/usr/bin/env octave
% convert_mat_tables.m
% MATLAB table オブジェクトを plain な数値配列に変換するスクリプト。
% MATLAB と Octave の両方で動作する。
%
% 使い方:
%   octave --no-gui convert_mat_tables.m <input.mat> <output.mat>
%   matlab -batch "convert_mat_tables <input.mat> <output.mat>"
%
% 効果:
%   入力 .mat ファイルの全 table 型変数を数値配列に変換し、
%   元の struct 階層にそのまま差し替えて保存する。
%   table でない変数 (numeric, struct 等) はそのまま保持される。
%
% テーブル検出の仕組み (3段階):
%   1. istable() による検出 (MATLAB)
%   2. class(obj)=='table' による検出 (Octave 一部バージョン)
%   3. struct の内部フィールド (data/varDim/rowDim, Properties) による検出
%      (Octave が table を struct としてロードするケース)
%
% これにより scipy.io.loadmat() で全データを plain ndarray として読み込める。

function main()
  args = argv();
  if length(args) < 2
    fprintf('Usage: octave --no-gui convert_mat_tables.m <input.mat> <output.mat>\n');
    exit(1);
  end
  input_file = args{1};
  output_file = args{2};

  fprintf('Loading: %s\n', input_file);
  data = load(input_file);

  field_names = fieldnames(data);
  n_converted = 0;

  for i = 1:length(field_names)
    fname = field_names{i};
    [data.(fname), count] = convert_recursive(data.(fname), fname);
    n_converted = n_converted + count;
  end

  fprintf('Converted %d table(s) to numeric arrays.\n', n_converted);
  fprintf('Saving: %s\n', output_file);

  % -v7 形式で保存 (scipy.io.loadmat 互換)
  save('-v7', output_file, '-struct', 'data');

  fprintf('Done.\n');
end


% ======== Table detection (MATLAB/Octave 互換) ========
function is_tbl = check_is_table(obj)
  is_tbl = false;
  % Method 1: istable (MATLAB)
  try
    is_tbl = istable(obj);
    return;
  catch
  end
  % Method 2: class name check (Octave with partial table support)
  try
    is_tbl = strcmp(class(obj), 'table');
  catch
  end
end


% ======== Numeric column extraction ========
function [arr, success] = extract_columns(obj, col_names, path)
  success = false;
  arr = [];
  cols = {};
  for j = 1:length(col_names)
    try
      col = obj.(col_names{j});
      if isnumeric(col)
        cols{end+1} = col(:);
      else
        fprintf('    [skip] Non-numeric: %s.%s\n', path, col_names{j});
      end
    catch e
      fprintf('    [skip] Access error: %s.%s: %s\n', path, col_names{j}, e.message);
    end
  end
  if ~isempty(cols)
    arr = horzcat(cols{:});
    success = true;
  end
end


% ======== Main recursive converter ========
function [result, count] = convert_recursive(obj, path)
  count = 0;

  % --- (1) Class-based table detection (MATLAB / Octave) ---
  if check_is_table(obj)
    % Try table2array first
    try
      result = table2array(obj);
      count = 1;
      fprintf('  [table->array] %s: %dx%d\n', path, size(result, 1), size(result, 2));
      return;
    catch
    end
    % Try Properties.VariableNames
    try
      vnames = obj.Properties.VariableNames;
      [result, success] = extract_columns(obj, vnames, path);
      if success
        count = 1;
        fprintf('  [table->array] %s: %dx%d (column-by-column)\n', path, size(result, 1), size(result, 2));
        return;
      end
    catch
    end
    result = obj;
    fprintf('  [warning] Cannot convert table at %s\n', path);
    return;
  end

  % --- (2) Detect table loaded as struct by Octave ---
  %     Octave が MATLAB table を struct としてロードした場合に対応
  if isstruct(obj) && isscalar(obj)
    fnames = fieldnames(obj);

    % Pattern A: 内部表現 (data cell array + varDim/rowDim)
    if isfield(obj, 'data') && iscell(obj.data) && ...
       (isfield(obj, 'varDim') || isfield(obj, 'rowDim') || isfield(obj, 'arrayProps'))
      cols = {};
      for j = 1:numel(obj.data)
        if isnumeric(obj.data{j})
          cols{end+1} = obj.data{j}(:);
        end
      end
      if ~isempty(cols)
        result = horzcat(cols{:});
        count = 1;
        fprintf('  [table-struct->array] %s: %dx%d\n', path, size(result, 1), size(result, 2));
        return;
      end
    end

    % Pattern B: Properties.VariableNames フィールドが存在
    if isfield(obj, 'Properties')
      try
        props = obj.Properties;
        if isstruct(props) && isfield(props, 'VariableNames')
          vnames = props.VariableNames;
          if iscell(vnames) && ~isempty(vnames)
            [arr, success] = extract_columns(obj, vnames, path);
            if success
              result = arr;
              count = 1;
              fprintf('  [table-props->array] %s: %dx%d\n', path, size(result, 1), size(result, 2));
              return;
            end
          end
        end
      catch
      end
    end
  end

  % --- (3) Normal struct: recurse ---
  if isstruct(obj)
    fnames = fieldnames(obj);
    for idx = 1:numel(obj)
      for j = 1:length(fnames)
        fn = fnames{j};
        subpath = sprintf('%s.%s', path, fn);
        try
          [obj(idx).(fn), c] = convert_recursive(obj(idx).(fn), subpath);
          count = count + c;
        catch e
          fprintf('  [warning] Skip field conversion at %s: %s\n', subpath, e.message);
        end
      end
    end
    result = obj;
    return;
  end

  % --- (4) Cell array: recurse ---
  if iscell(obj)
    for j = 1:numel(obj)
      subpath = sprintf('%s{%d}', path, j);
      [obj{j}, c] = convert_recursive(obj{j}, subpath);
      count = count + c;
    end
    result = obj;
    return;
  end

  % --- (5) Other (numeric, char, etc.) ---
  result = obj;
end

main();
