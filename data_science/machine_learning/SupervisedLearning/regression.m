% Import data
data = readtable('irisPlusRandom.csv');
summary(data)

% Preallocate a matrix for the first five columns
numColumns = 5;
numRows = height(data);
numericData = zeros(numRows, numColumns);

% Convert each column to numeric
for i = 1:numColumns
    col = data.(i); % Get the i-th column
    if iscell(col)
        % Convert cells to numbers (assuming they contain numeric data as strings)
        col = cellfun(@str2double, col);
    elseif ischar(col) || isstring(col)
        % If the column is a character array or string array
        col = str2double(col);
    end
    % Fill the numericData matrix
    numericData(:, i) = col;
end

%{
numColumns = width(data);

% Iterate through all columns except the last one
for i = 1:numColumns-1
    currentColumn = data.(i);
    if iscell(currentColumn)
        % Convert cell array of strings to char array for processing
        charArray = char(currentColumn);
        % Replace ',' with '.' and then convert to double
        currentColumn = str2double(strrep(charArray, ',', '.'));
        % Assign the converted column back to the table
        data.(i) = currentColumn;
    elseif isnumeric(currentColumn)
        % If the column is already numeric, ensure it's of type double
        data.(i) = double(currentColumn);
    end
end

%}

% Now, proceed with the regression
features = numericData(2:145, 2:5);
response = numericData(2:145, 1:1);

% Create and fit the model
mdl = fitlm(features, response);

plot(mdl)