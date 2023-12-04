% Import data
data = readtable('irisPlusRandom.csv');
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

% Now, proceed with the regression
features = data(1:145, 2:5);
response = data.sepal_length(1:145);

% Create and fit the model
mdl = fitlm(features, response);